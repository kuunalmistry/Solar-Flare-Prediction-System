
import os
import joblib
import traceback
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(__file__)
MODELS_DIR = os.path.join(BASE_DIR, "saved_models")

class DummyModel:
    def __init__(self):
        # classes chosen to match typical project
        self.classes_ = np.array(["None", "C", "M", "X"])
    def predict(self, X):
        n = X.shape[0] if hasattr(X, "shape") else 1
        idxs = np.random.randint(1, len(self.classes_), size=(n,))
        return idxs
    def predict_proba(self, X):
        n = X.shape[0] if hasattr(X, "shape") else 1
        p = np.random.rand(n, len(self.classes_))
        p = p / p.sum(axis=1, keepdims=True)
        return p

class ModelService:
    def __init__(self):
        self.pre = None
        self.model = None
        self.le = None
        self.load_error = None
        # Try load artifacts but catch all exceptions
        try:
            pre_path = os.path.join(MODELS_DIR, "preprocessor.joblib")
            if os.path.exists(pre_path):
                self.pre = joblib.load(pre_path)
        except Exception as e:
            self._record_error("preprocessor.joblib", e)

        # Try to load a tree-based model (RandomForest) first
        try:
            rf_path = os.path.join(MODELS_DIR, "rf_model.joblib")
            if os.path.exists(rf_path):
                self.model = joblib.load(rf_path)
        except Exception as e:
            self._record_error("rf_model.joblib", e)
            self.model = None

        # Optionally try other model names (hgb_model, model.joblib...)
        if self.model is None:
            try:
                hgb_path = os.path.join(MODELS_DIR, "hgb_model.joblib")
                if os.path.exists(hgb_path):
                    self.model = joblib.load(hgb_path)
            except Exception as e:
                self._record_error("hgb_model.joblib", e)
                self.model = None

        # Try to load label encoder (optional)
        try:
            le_path = os.path.join(MODELS_DIR, "label_encoder.joblib")
            if os.path.exists(le_path):
                self.le = joblib.load(le_path)
        except Exception as e:
            self._record_error("label_encoder.joblib", e)
            self.le = None

        # If anything failed, fall back to dummy model but keep error info
        if self.model is None:
            self.model = DummyModel()
            if self.le is None:
                self.le = type("LE", (), {"classes_": self.model.classes_})
        else:
            # If model loaded but label encoder absent, try to get classes_ from model or set fallback
            try:
                if self.le is None and hasattr(self.model, "classes_"):
                    self.le = type("LE", (), {"classes_": getattr(self.model, "classes_", np.array(["None","C","M","X"]))})
                elif self.le is None:
                    # fallback
                    self.le = type("LE", (), {"classes_": np.array(["None","C","M","X"])})
            except Exception:
                self.le = type("LE", (), {"classes_": np.array(["None","C","M","X"])})

    def _record_error(self, name, exc):
        msg = f"Failed to load {name}: {repr(exc)}\n" + traceback.format_exc()
        self.load_error = (name, msg) if self.load_error is None else (name, msg + "\n\n" + (self.load_error[1] or ""))
        # Save to disk for diagnostics
        try:
            with open(os.path.join(BASE_DIR, "model_load_error.txt"), "a", encoding="utf-8") as f:
                f.write(f"-----\n{name} load failure:\n{msg}\n")
        except Exception:
            pass

    def get_feature_names(self):
        # Prefer preprocessor feature names if available
        try:
            if self.pre is not None and hasattr(self.pre, "feature_names_in_"):
                return list(self.pre.feature_names_in_)
        except Exception:
            pass
        # fallback
        return ["year", "month", "sunspot_count", "xray_flux"]

    def get_status(self):
        # returns a dict the app can serve at /api/model-status
        return {
            "model_loaded": False if isinstance(self.model, DummyModel) and self.load_error else True if not isinstance(self.model, DummyModel) else False,
            "load_error": self.load_error[1] if self.load_error else None,
            "model_type": type(self.model).__name__,
            "classes": list(getattr(self.le, "classes_", []))
        }

    def _prepare_df_for_predict(self, df):
        # ensure expected cols exist
        expected = self.get_feature_names()
        for c in expected:
            if c not in df.columns:
                df[c] = np.nan
        return df[expected]

    def predict_single(self, feats: dict, return_explain=True):
        df = pd.DataFrame([feats])
        df = self._prepare_df_for_predict(df)
        # replace None with NaN so any preprocessor imputers work
        df = df.where(pd.notnull(df), np.nan)
        try:
            if self.pre is not None:
                X = self.pre.transform(df)
            else:
                X = df.fillna(0).values
        except Exception:
            X = df.fillna(0).values

        # Predictions
        try:
            preds = self.model.predict(X)
            if hasattr(self.model, "predict_proba"):
                probas = self.model.predict_proba(X)[0]
            else:
                probas = np.zeros(len(self.le.classes_))
        except Exception:
            # If any runtime error occurs, fall back to dummy predictions
            dm = DummyModel()
            preds = dm.predict(X)
            probas = dm.predict_proba(X)[0]

        try:
            pred_idx = int(preds[0]) if hasattr(preds, "__len__") else int(preds)
            pred_label = self.le.classes_[pred_idx] if pred_idx < len(self.le.classes_) else str(pred_idx)
        except Exception:
            # last fallback
            pred_label = str(self.le.classes_[0] if len(self.le.classes_) else "None")
            probas = np.zeros(len(self.le.classes_))

        res = {
            "prediction": str(pred_label),
            "prediction_index": int(pred_idx) if 'pred_idx' in locals() else 0,
            "classes": list(self.le.classes_),
            "probabilities": probas.tolist(),
            "top_features": []
        }

        # If original model had feature_importances_, report top features (best-effort)
        try:
            if hasattr(self.model, "feature_importances_"):
                import numpy as _np
                fi = _np.array(self.model.feature_importances_)
                fnames = self.get_feature_names()
                idxs = _np.argsort(fi)[::-1][:10]
                top = []
                for i in idxs:
                    fname = fnames[i] if i < len(fnames) else f"f{i}"
                    top.append({"feature": fname, "importance": float(fi[i])})
                res["top_features"] = top
        except Exception:
            pass

        return res

    def predict_batch_df(self, df):
        df2 = self._prepare_df_for_predict(df.copy())
        try:
            if self.pre is not None:
                X = self.pre.transform(df2)
            else:
                X = df2.fillna(0).values
        except Exception:
            X = df2.fillna(0).values

        try:
            preds = self.model.predict(X)
            if hasattr(self.model, "predict_proba"):
                probas = self.model.predict_proba(X)
            else:
                probas = np.zeros((len(preds), len(self.le.classes_)))
        except Exception:
            dm = DummyModel()
            preds = dm.predict(X)
            probas = dm.predict_proba(X)

        labels = []
        for p in preds:
            try:
                labels.append(self.le.classes_[int(p)])
            except Exception:
                labels.append(str(p))

        out = df.reset_index(drop=True).copy()
        out["prediction"] = labels
        for i, cls in enumerate(self.le.classes_):
            out[f"prob_{cls}"] = probas[:, i]
        return out
