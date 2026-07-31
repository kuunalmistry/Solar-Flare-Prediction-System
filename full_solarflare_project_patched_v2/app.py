
import os
import json
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
from docx import Document
import pandas as pd

from model_service import ModelService

BASE_DIR = os.path.dirname(__file__)
SAVED_MODELS_DIR = os.path.join(BASE_DIR, 'saved_models')
STATIC_PLOTS_DIR = os.path.join(BASE_DIR, 'static', 'plots')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
METRICS_PATH = os.path.join(BASE_DIR, 'metrics.json')

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

try:
    model_service = ModelService()
except Exception as e:
    model_service = None
    print("Warning: ModelService initialization failed:", e)

def docx_to_html(path):
    doc = Document(path)
    html_parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            html_parts.append(f'<p>{text}</p>')
    return '\n'.join(html_parts)

@app.route('/')
def index():
    metrics = {}
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH) as f:
            metrics = json.load(f)
    return render_template('index.html', metrics=metrics)

@app.route('/report')
def report():
    report_file = None
    if os.path.exists(REPORTS_DIR):
        for fname in sorted(os.listdir(REPORTS_DIR)):
            if fname.lower().endswith('.docx'):
                report_file = os.path.join(REPORTS_DIR, fname)
                break
    if not report_file:
        return "No report available", 404
    html = docx_to_html(report_file)
    return render_template('report.html', report_html=html, report_name=os.path.basename(report_file))

@app.route('/eda')
def eda():
    plots = []
    if os.path.exists(STATIC_PLOTS_DIR):
        for fname in sorted(os.listdir(STATIC_PLOTS_DIR)):
            plots.append(f'/static/plots/{fname}')
    return render_template('eda.html', plots=plots)

@app.route('/models')
def models_page():
    metrics = {}
    if os.path.exists(METRICS_PATH):
        with open(METRICS_PATH) as f:
            metrics = json.load(f)
    model_files = []
    if os.path.exists(SAVED_MODELS_DIR):
        model_files = sorted(os.listdir(SAVED_MODELS_DIR))
    return render_template('models.html', metrics=metrics, model_files=model_files)

@app.route('/download-model/<filename>')
def download_model(filename):
    return send_from_directory(SAVED_MODELS_DIR, filename, as_attachment=True)

@app.route('/download-report/<filename>')
def download_report(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)

@app.route('/predict')
def predict_ui():
    return render_template('predict.html')

@app.route('/api/predict-future', methods=['POST'])
def predict_future():
    if not model_service:
        return jsonify({'error': 'Model service unavailable'}), 503
    data = request.get_json(force=True)
    try:
        year = int(data.get('year'))
        month = int(data.get('month'))
    except Exception:
        return jsonify({'error': 'year and month required as integers'}), 400

    # Simple synthetic feature construction for future prediction:
    feats = {}
    # If preprocessor has feature_names_in_, try to build those names with defaults.
    if hasattr(model_service.pre, 'feature_names_in_'):
        for col in model_service.pre.feature_names_in_:
            # basic heuristics to fill features
            lname = col.lower()
            if 'year' in lname:
                feats[col] = year
            elif 'month' in lname:
                feats[col] = month
            elif 'sunspot' in lname:
                feats[col] = 50 + ((year + month) % 40)
            elif 'xray' in lname or 'x_ray' in lname or 'xray_flux' in lname:
                feats[col] = 1.0 + ((month % 5) * 0.2)
            else:
                # fallback NaN or neutral
                feats[col] = None
    else:
        # Minimal feature set
        feats = {
            'year': year,
            'month': month,
            'sunspot_count': 50 + ((year + month) % 40),
            'xray_flux': 1.0 + ((month % 5) * 0.2)
        }

    try:
        res = model_service.predict_single(feats, return_explain=True)
        return jsonify(res)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/features', methods=['GET'])
def api_features():
    if not model_service:
        return jsonify({'error': 'Model service unavailable'}), 503
    features = model_service.get_feature_names()
    if not features and hasattr(model_service.pre, 'feature_names_in_'):
        features = list(model_service.pre.feature_names_in_)
    return jsonify({'features': features})

@app.route('/api/predict', methods=['POST'])
def api_predict():
    if not model_service:
        return jsonify({'error': 'Model service unavailable'}), 503
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({'error': 'Invalid JSON'}), 400
    if not isinstance(data, dict):
        return jsonify({'error': 'Send JSON object mapping feature->value'}), 400
    try:
        res = model_service.predict_single(data, return_explain=True)
        return jsonify(res)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/predict-csv', methods=['POST'])
def api_predict_csv():
    if not model_service:
        return jsonify({'error': 'Model service unavailable'}), 503
    if 'file' not in request.files:
        return jsonify({'error': 'CSV file required in field "file"'}), 400
    f = request.files['file']
    try:
        df = pd.read_csv(f)
    except Exception as e:
        try:
            f.stream.seek(0)
            df = pd.read_csv(f, encoding='latin1')
        except Exception as e2:
            return jsonify({'error': 'Failed to parse CSV: ' + str(e2)}), 400
    try:
        out_df = model_service.predict_batch_df(df)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return out_df.to_csv(index=False), 200, {'Content-Type': 'text/csv; charset=utf-8'}


@app.route('/team')
def team():
    members = [
        {'name': 'Megh', 'role': 'Team Lead', 'photo': 'megh.png'},
        {'name': 'Aayushi', 'role': 'Model Development', 'photo': 'aayushi.png'},
        {'name': 'Kuunal', 'role': 'Research', 'photo': 'kuunal.png'},
        {'name': 'Ruchita', 'role': 'Report Making', 'photo': 'ruchita.png'},
        {'name': 'Manvith', 'role': 'Research', 'photo': 'manvith.png'},
    ]
    return render_template('team.html', team=members)


@app.route('/downloads')
def downloads():
    return render_template('downloads.html')



@app.route('/api/model-status', methods=['GET'])
def api_model_status():
    try:
        status = model_service.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({'model_loaded': False, 'load_error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
