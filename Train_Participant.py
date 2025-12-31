import hashlib
import time
import joblib
import requests
import os
from web3 import Web3
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import StandardScaler
import os
from dotenv import load_dotenv


load_dotenv()

MY_WALLET = os.getenv("WALLET_ADDRESS")
MY_PRIVATE_KEY = os.getenv("PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDR = os.getenv("CONTRACT_ADDRESS")
SERVER_URL = os.getenv("SERVER_URL")
# Web3 Initialization
# Web3 Initialization
web3 = Web3(Web3.HTTPProvider(RPC_URL))

# Complete ABI for functions required by the participant
ABI = [
    {"inputs": [{"internalType": "bytes32","name": "_modelHash","type": "bytes32"}],"name": "submitUpdate","outputs": [],"stateMutability": "nonpayable","type": "function"},
    {"inputs": [],"name": "trainingActive","outputs": [{"internalType": "bool","name": "","type": "bool"}],"stateMutability": "view","type": "function"},
    {"inputs": [],"name": "currentRound","outputs": [{"internalType": "uint256","name": "","type": "uint256"}],"stateMutability": "view","type": "function"},
    {"inputs": [{"type": "uint256"}, {"type": "address"}], "name": "contributions", "outputs": [{"type": "bytes32", "name": "modelHash"}, {"type": "bool", "name": "isValidated"}, {"type": "bool", "name": "isPaid"}], "stateMutability": "view", "type": "function"}
]

contract = web3.eth.contract(address=CONTRACT_ADDR, abi=ABI)

def download_global_model():
    """Downloads the latest version of the aggregated model."""
    url = f"{SERVER_URL}/static/global_model.joblib"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            with open("base_model.joblib", "wb") as f:
                f.write(response.content)
            print("📥 Modèle global téléchargé.")
            return joblib.load("base_model.joblib")
    except:
        print("ℹ️ Initialisation : nouveau modèle créé.")
    return None

def train_and_automate(round_number):
    print(f"\n┏━━ 🚀 ROUND {round_number} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    
    # 1. Security Check
    if not contract.functions.trainingActive().call():
        print("┃ ❌ Erreur : L'entraînement n'est pas actif sur la blockchain.")
        return False
        
    # 2. Check if already contributed on-chain
    contrib = contract.functions.contributions(round_number, MY_WALLET).call()
    # contrib = (modelHash, isValidated, isPaid)
    if int.from_bytes(contrib[0], 'big') != 0:
        print(f"┃ ✅ Déjà contribué pour le Round {round_number} (Hash sur chaine). Skip.")
        return True

    # 3. PREPARATION AND LOCAL TRAINING
    try:
        # Loading the local dataset SPECIFIC to the client
        df = pd.read_csv("datasets/client_A.csv")
        df = df.head(600)
        X = df.drop(['Churn', 'customerID'], axis=1)
        y = df['Churn']

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        
        # Split locally to have an internal validation set
        X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2)
        
        # Download the Global Model
        global_model = download_global_model()
        
        print("┃ 🤖 Entraînement local (LogisticRegression)...")
        # We use warm_start=True to potentially reuse weights (simulated)
        # Note: For a real FedAvg with sklearn, we initialize coefficients if possible
        local_model = LogisticRegression(max_iter=1000, warm_start=True)
        
        # MERGE / INITIALIZATION : Global -> Local (FedAvg step 1: Broadcast)
        if global_model:
            print(f"┃    ↳ 📥 Initialisation avec les poids du modèle global")
            try:
                # We must 'trick' sklearn to accept manual initialization
                # 1. Set attributes
                local_model.coef_ = global_model.coef_
                local_model.intercept_ = global_model.intercept_
                local_model.classes_ = global_model.classes_
            except Exception as e:
                print(f"⚠️ Erreur init poids : {e}")
        
        # Local training (will refine weights if warm_start works well with the default solver)
        local_model.fit(X_train, y_train)
        
        acc = accuracy_score(y_test, local_model.predict(X_test))
        filename = f'model_weights_{MY_WALLET}.joblib'
        joblib.dump(local_model, filename)
        print(f"┃    ↳ 📊 Précision : {acc * 100:.2f}%")
        print(f"┃    ↳ 💾 Modèle sauvegardé : {filename}")

        # 3. CALCULATE HASH
        sha256_hash = hashlib.sha256()
        with open(filename, "rb") as f:
            sha256_hash.update(f.read())
        hash_result = "0x" + sha256_hash.hexdigest()
        # print(f"┃    ↳ #️⃣ Hash : {hash_result[:10]}...")

        # 4. BLOCKCHAIN
        print("┃ 🔗 Envoi du hash sur Sepolia...")
        nonce = web3.eth.get_transaction_count(MY_WALLET, 'pending')
        tx = contract.functions.submitUpdate(hash_result).build_transaction({
            'from': MY_WALLET, 'nonce': nonce, 'gas': 200000, 'gasPrice': web3.eth.gas_price
        })
        signed_tx = web3.eth.account.sign_transaction(tx, MY_PRIVATE_KEY)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        
        print(f"┃    ↳ ✔️ Tx envoyée : {web3.to_hex(tx_hash)[:20]}...")
        print("┃    ↳ ⏩ Envoi immédiat (Confirmation asynchrone par le Bot)...")
        
        # web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300) 
        # print("┃    ↳ ✅ Preuve ancrée sur la Blockchain.")

        # 5. SEND TO SERVER
        print("┃ 📤 Transfert du fichier au coordinateur...")
        with open(filename, "rb") as f:
            files = {"file": f}
            data = {"participant_address": MY_WALLET, "accuracy": acc}
            requests.post(f"{SERVER_URL}/upload", files=files, data=data)
        
        print(f"┗━━ 🏁 Round {round_number} Terminé ! ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
        return True

    except Exception as e:
        print(f"┃ ❌ Erreur critique : {e}")
        print("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
        return False

def monitor_mode():
    print("🛰️ Mode automatique activé. En attente des rounds...")
    last_processed_round = -1
    while True:
        try:
            # Ask the server if there is an active round
            res = requests.get(f"{SERVER_URL}/status", timeout=5).json()
            if res["training_active"] and res["current_round"] > last_processed_round:
                if train_and_automate(res["current_round"]):
                    last_processed_round = res["current_round"]
        except Exception as e:
            print("⚠️ Serveur injoignable, nouvelle tentative...")
        time.sleep(2)

if __name__ == "__main__":
    monitor_mode()