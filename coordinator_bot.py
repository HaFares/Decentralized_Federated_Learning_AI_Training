import os
import time
import shutil
import hashlib
import requests
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURATION ---
COORD_ADDR = os.getenv("WALLET_ADDRESS")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDR = os.getenv("CONTRACT_ADDRESS")
SERVER_URL = "http://127.0.0.1:8000"

web3 = Web3(Web3.HTTPProvider(RPC_URL))

# Complete ABI for round management
ABI = [
    {"inputs": [], "name": "currentRound", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "uint256"}, {"type": "address"}], "name": "contributions", "outputs": [{"type": "bytes32", "name": "modelHash"}, {"type": "bool", "name": "isValidated"}, {"type": "bool", "name": "isPaid"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "address", "name": "_participant"}], "name": "validateAndPay", "outputs": [], "stateMutability": "payable", "type": "function"}
]

contract = web3.eth.contract(address=CONTRACT_ADDR, abi=ABI)

def verify_and_pay(addr, path, current_round):
    try:
        # 1. Recalculate local hash
        with open(path, "rb") as f: 
            local_hash = "0x" + hashlib.sha256(f.read()).hexdigest()
        
        # 2. Read specific mapping (Current Round + Participant Address)
        check_addr = Web3.to_checksum_address(addr)
        data = contract.functions.contributions(current_round, check_addr).call()
        
        on_chain_hash = web3.to_hex(data[0])
        is_paid = data[2]

        # If no hash is recorded for this round
        if on_chain_hash == "0x" + "0"*64: 
            return False 
            
        if is_paid: 
            # Notify server it is VERIFIED (Even if already paid before/Mismatch ignored)
            print(f"      ↳ 📡 Déjà payé. Serveur notifié (Sync).")
            try:
                requests.post(f"{SERVER_URL}/webhook/verify_contribution", 
                              json={"participant_address": addr, "round": current_round}, timeout=5)
            except: pass
            return True 
            
        print(f"   👤 {addr[:10]}... | Hash Chain: {on_chain_hash[:10]}... | Local: {local_hash[:10]}...")

        # 4. Comparison and Payment
        if local_hash.lower() == on_chain_hash.lower():
            print(f"      ↳ ✅ Hash Valide. Envoi du paiement...")
            
            # Using 'pending' to chain payments without nonce errors
            nonce = web3.eth.get_transaction_count(COORD_ADDR, 'pending')
            
            tx = contract.functions.validateAndPay(check_addr).build_transaction({
                "from": COORD_ADDR, 
                "value": web3.to_wei(0.00001, "ether"),
                "gas": 300000, 
                "gasPrice": int(web3.eth.gas_price * 1.1), 
                "nonce": nonce
            })
            
            signed = web3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
            print(f"      ↳ 💰 Transaction envoyée : {web3.to_hex(tx_hash)}")
            
            # --- NOTIFICATION TO SERVER ---
            try:
                requests.post(f"{SERVER_URL}/webhook/verify_contribution", 
                              json={"participant_address": addr, "round": current_round})
                print(f"      ↳ 📢 Serveur notifié.")
            except Exception as e:
                print(f"      ↳ ⚠️ Erreur notif serveur : {e}")

            return True
        else:
            print(f"      ↳ ❌ FRAUDE : Hash mismatch!")
            return True 
            
    except Exception as e: 
        print(f"      ↳ ⚠️ Erreur technique : {e}")
        return False

import re

def start_bot():
    print("┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print("┃ 🤖 BOT DE PAIEMENT ACTIF               ┃")
    print("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
    
    received_dir = "received_models"
    processed_dir = os.path.join(received_dir, "processed")
    if not os.path.exists(processed_dir): os.makedirs(processed_dir)

    last_round_seen = -1
    processed_cache = set()

    while True:
        try:
            # Get current round ONLY ONCE per scan cycle for consistency
            current_round_on_chain = contract.functions.currentRound().call()
            
            if current_round_on_chain != last_round_seen:
                print(f"\n🔄 --- SCANNING ROUND {current_round_on_chain} ---")
                last_round_seen = current_round_on_chain
                processed_cache.clear() # New round, we reset the cache

        except:
            print("⚠️ Impossible de lire le round actuel sur la blockchain.")
            time.sleep(5)
            continue

        files = [f for f in os.listdir(received_dir) if f.endswith(".joblib")]
        
        for f in files:
            path = os.path.join(received_dir, f)
            match = re.match(r"round_(\d+)_(0x[a-fA-F0-9]{40})\.joblib", f)
            
            if match:
                file_round = int(match.group(1))
                addr = match.group(2)
                
                # We only process files from the CURRENT Round
                if file_round == current_round_on_chain:
                    if addr in processed_cache:
                        continue # Already processed for this round, ignore
                        
                    if verify_and_pay(addr, path, current_round_on_chain):
                        processed_cache.add(addr) # Mark as processed

        time.sleep(2) # Pause of 2 seconds between scans

if __name__ == "__main__": 
    start_bot()
