from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import shutil
import os
import uvicorn
from web3 import Web3
from agreggate import aggregate_and_publish 

app = FastAPI(title="Orchestrateur FL Automatique")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BLOCKCHAIN CONFIGURATION -

RPC_URL = os.getenv("RPC_URL")
CONTRACT_ADDR = os.getenv("CONTRACT_ADDRESS")
COORD_ADDR = Web3.to_checksum_address("Adress of the Coordinator")
PRIVATE_KEY =  os.getenv("PRIVATE_KEY")



web3 = Web3(Web3.HTTPProvider(RPC_URL))
# Minimal ABI for control functions
ABI = [
    {"inputs": [], "name": "startNewRound", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "currentRound", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}
]
contract = web3.eth.contract(address=CONTRACT_ADDR, abi=ABI)

# Serve static folder
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

state = {
    "training_active": False,
    "current_round": 0,
    "target_rounds": 0,
    "expected_participants": 0,
    "received_this_round": 0,
    "metrics": []
}

UPLOAD_FOLDER = "received_models"
if not os.path.exists(UPLOAD_FOLDER): os.makedirs(UPLOAD_FOLDER)

# --- GLOBAL MODEL EVALUATION ---
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, precision_score, recall_score, f1_score
from sklearn.linear_model import LogisticRegression
import joblib
import numpy as np

# Loading data at startup (Global - Test Set Only)
try:
    df_test = pd.read_csv("datasets/server_test.csv")
    
    X_global_test_raw = df_test.drop(['Churn', 'customerID'], axis=1)

    y_global_test = df_test['Churn']
    
    # Scaling (Important for Logistic Regression)
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_global_test = scaler.fit_transform(X_global_test_raw)

    print(f"✅ Données de test serveur chargées et SCALÉES: {X_global_test.shape}")
except Exception as e:
    print(f"⚠️ Erreur chargement dataset serveur: {e}")
    # Fallback in case (should not happen if setup_datasets.py ran)
    X_global_test = []
    y_global_test = []

def calculate_metrics(model, X, y):
    """Calculates complete metrics for a given model."""
    try:
        y_pred = model.predict(X)
        y_prob = model.predict_proba(X)
        
        return {
            "accuracy": float(accuracy_score(y, y_pred)),
            "loss": float(log_loss(y, y_prob)),
            "precision": float(precision_score(y, y_pred, average='macro')),
            "recall": float(recall_score(y, y_pred, average='macro')),
            "f1": float(f1_score(y, y_pred, average='macro'))
        }
    except Exception as e:
        print(f"⚠️ Erreur Metrics : {e}")
        return {"accuracy": 0.0, "loss": 99.9, "precision": 0.0, "recall": 0.0, "f1": 0.0}

def evaluate_global_model():
    """Loads the global model and evaluates it on the server test set."""
    model_path = "static/global_model.joblib"
    if not os.path.exists(model_path): return None
    
    model = joblib.load(model_path)
    metrics = calculate_metrics(model, X_global_test, y_global_test)
    
    print(f"⭐ Global Model Results -> Acc: {metrics['accuracy']:.2f}, F1: {metrics['f1']:.2f}, Loss: {metrics['loss']:.2f}")
    return metrics

def sync_blockchain_round():
    """Calls the Smart Contract to move to the next round on Sepolia."""
    try:
        print(f"🔗 Synchronisation Blockchain : Activation du Round...")
        nonce = web3.eth.get_transaction_count(COORD_ADDR, 'pending')
        # Increase gasPrice by 30% to ensure fast validation
        gas_price = int(web3.eth.gas_price * 1.3)
        
        tx = contract.functions.startNewRound().build_transaction({
            'from': COORD_ADDR,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': gas_price
        })
        signed = web3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = web3.eth.send_raw_transaction(signed.raw_transaction)
        
        # Crucial wait for participant and bot to see the change
        web3.eth.wait_for_transaction_receipt(tx_hash)
        blockchain_round = contract.functions.currentRound().call()
        print(f"✅ Blockchain synchronisée au Round {blockchain_round}")
        return blockchain_round
    except Exception as e:
        print(f"⚠️ Erreur de synchronisation Blockchain : {e}")
        return None

@app.get("/status")
async def get_status():
    return state

@app.post("/control/start_auto")
def start_auto(rounds: int, participants: int):
    # ... (content remains implicitly same, just header change)
    # Activate New Round on Blockchain AND get its real number
    new_round = sync_blockchain_round()
    
    if new_round is None:
        return {"status": "error", "message": "Blockchain sync failed"}

    state["training_active"] = True
    state["current_round"] = new_round
    # Target is no longer "3" but "Current Round + 3"
    state["target_rounds"] = new_round + rounds - 1
    
    state["expected_participants"] = participants
    state["received_this_round"] = 0
    
    print(f"🚀 Session automatique lancée : Rounds {new_round} à {state['target_rounds']}")
    return {"status": "started", "start_round": new_round}

@app.post("/control/stop")
def stop_round():
    state["training_active"] = False
    return {"status": "stopped"}

from pydantic import BaseModel
import requests

class VerifyPayload(BaseModel):
    participant_address: str
    round: int

@app.post("/webhook/verify_contribution")
def verify_contribution(payload: VerifyPayload):
    """Called by the Bot when a participant is confirmed on Blockchain."""
    # Safety Check: Ignore if system is stopped
    if not state["training_active"]:
        return {"status": "inactive"}

    print(f"🔐 Webhook: Verifying {payload.participant_address} for Round {payload.round}")
    
    # 1. Update verification status
    updated = False
    for m in state["metrics"]:
        if m["round"] == payload.round and m["participant"] == payload.participant_address:
            m["verified"] = True
            updated = True
            break
            
    if not updated:
        return {"status": "ignored"}

    # 2. Check logic (Aggregation)
    # Ensure we don't re-aggregate the SAME round multiple times
    if state["current_round"] in state.get("aggregated_rounds", []):
        return {"status": "already_aggregated"}

    current_round_metrics = [m for m in state["metrics"] if m["round"] == state["current_round"] and m.get("participant") != "GLOBAL_MODEL"]
    verified_count = sum(1 for m in current_round_metrics if m.get("verified"))
    

    if verified_count >= state["expected_participants"]:
        # LOCK: Mark round as aggregated immediately to prevent race conditions
        if "aggregated_rounds" not in state: state["aggregated_rounds"] = []
        state["aggregated_rounds"].append(state["current_round"])

        print(f"🔄 Round {state['current_round']} Verified & Complete. Aggregating...")
        
        # Collect ONLY verified files
        prefix = f"round_{state['current_round']}_"
        files = []
        for m in current_round_metrics:
            if m.get("verified"):
                fpath = f"{UPLOAD_FOLDER}/round_{state['current_round']}_{m['participant']}.joblib"
                if os.path.exists(fpath):
                    files.append(fpath)
        
        if files:
            aggregate_and_publish(files, round_num=state['current_round'])
            
            # Global Model Evaluation
            global_metrics = evaluate_global_model()
            if global_metrics:
                entry = {"round": state["current_round"], "participant": "GLOBAL_MODEL"}
                entry.update(global_metrics)
                state["metrics"].append(entry)
            
            # Next Round Logic
            if state["current_round"] < state["target_rounds"]:
                next_round = sync_blockchain_round()
                if next_round:
                    state["current_round"] = next_round
                    state["received_this_round"] = 0
                    print(f"➡️ Passage automatique au Round {state['current_round']}")
                else:
                    print("⚠️ Erreur Critique : Impossible de synchro le round suivant.")
                    state["training_active"] = False
            else:
                state["training_active"] = False
                print("🏁 Entraînement terminé !")
        else:
             print("⚠️ Error: No files found for verified participants.")

    return {"status": "verified"}

@app.post("/upload")
def upload_weight(participant_address: str = Form(...), accuracy: float = Form(...), file: UploadFile = File(...)):
    if not state["training_active"]:
        raise HTTPException(status_code=403, detail="L'entraînement n'est pas actif.")

    file_location = f"{UPLOAD_FOLDER}/round_{state['current_round']}_{participant_address}.joblib"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # --- VALIDATION SERVEUR ---
    try:
        part_model = joblib.load(file_location)
        metrics = calculate_metrics(part_model, X_global_test, y_global_test)
        print(f"gh Validation (Server-Side) {participant_address} -> Acc: {metrics['accuracy']:.2f}")
    except Exception as e :
        print("ereur:{e}")
        metrics = {"accuracy": 0.0, "loss": 99.9, "precision": 0.0, "recall": 0.0, "f1": 0.0}

    metric_entry = {
        "round": state["current_round"], 
        "participant": participant_address,
        "verified": False,
        "accuracy": accuracy
    }
    
    # We add other metrics calculated by the server (Loss, F1, etc.)
    metric_entry["loss"] = metrics["loss"]
    metric_entry["f1"] = metrics["f1"]
    metric_entry["precision"] = metrics["precision"]
    metric_entry["recall"] = metrics["recall"]
    metric_entry["server_accuracy"] = metrics["accuracy"] # Backup for comparison
    
    state["metrics"].append(metric_entry)
    
    state["received_this_round"] += 1
    print(f"📩 Reçu {participant_address} (En attente de validation Blockchain...)")
    
    # REMOVED: Aggregation logic is now in /webhook/verify_contribution
            
    return {"message": "Pending Blockchain Verification"}

@app.get("/metrics")
async def get_metrics():
    return state["metrics"]

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)