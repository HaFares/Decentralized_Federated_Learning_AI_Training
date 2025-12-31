import joblib
import os
import numpy as np
from sklearn.linear_model import LogisticRegression

def aggregate_and_publish(file_list, output_path="static/global_model.joblib", round_num=None):
    """
    Merges Logistic Regression models using FedAvg (Average of weights).
    """
    if not file_list:
        print("❌ Liste de fichiers vide. Agrégation impossible.")
        return

    print(f"🔄 Agrégation (FedAvg) de {len(file_list)} modèles LR en cours...")
    
    # 1. Load all models
    models = [joblib.load(f) for f in file_list]
    
    # Basic validation to ensure they are compatible
    # We assume all models were trained on the same features/classes
    
    # 2. Initialize accumulators for weights and intercepts
    # We take the first model as a reference for shape
    ref_model = models[0]
    avg_coef = np.zeros_like(ref_model.coef_)
    avg_intercept = np.zeros_like(ref_model.intercept_)
    
    # 3. Sum of weights
    for m in models:
        avg_coef += m.coef_
        avg_intercept += m.intercept_
    
    # 4. Average
    avg_coef /= len(models)
    avg_intercept /= len(models)
    
    # 5. Create the Global model
    global_model = LogisticRegression()
    # We must define attributes manually because we do not fit
    global_model.coef_ = avg_coef
    global_model.intercept_ = avg_intercept
    global_model.classes_ = ref_model.classes_
    global_model.n_iter_ = np.mean([m.n_iter_ for m in models]) # Just for info
    
    # 6. Copy feature metadata (to avoid sklearn Warning)
    if hasattr(ref_model, 'feature_names_in_'):
        global_model.feature_names_in_ = ref_model.feature_names_in_
    if hasattr(ref_model, 'n_features_in_'):
        global_model.n_features_in_ = ref_model.n_features_in_
    
    # Note: For the model to be usable for predict, we often need to simulate a fit
    # or ensure all necessary properties are there.
    # Hack for sklearn: we set is_fitted via check_is_fitted or simply by having coef_
    
    # Ensure the 'static' folder exists
    if not os.path.exists("static"):
        os.makedirs("static")
    
    # Save the aggregated model (Latest version)
    joblib.dump(global_model, output_path)
    
    # Save history (Round Version)
    if round_num is not None:
        history_path = f"static/global_model_round_{round_num}.joblib"
        joblib.dump(global_model, history_path)
        print(f"📜 Historique sauvegardé : {history_path}")
    
    print("-" * 30)
    print(f"🚀 NOUVEAU MODÈLE FL (FedAvg) PUBLIÉ : {output_path}")
    print(f"Poids moyennés sur {len(models)} participants.")
    print("-" * 30)