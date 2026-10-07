# HVOS PC Browser (Base Edition) v0.7 / 2026-10-07
# API キーは config.json から読みます（初回起動時に入力）。
import base64
import io
import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime

import keyboard
import pyautogui
import qrcode
from flask import Flask, jsonify, render_template_string, request
from google import genai
from PIL import Image

# ==========================================
# 0. コンソール出力のUTF-8化（ASCIIエラー対策）
# ==========================================
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)

# プログラムと同じフォルダに設定ファイルを置く（管理者実行でも場所がずれない）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_CONFIG_FILE = os.path.join(BASE_DIR, "config.json")  # APIキー・選択モデル（他人に渡さない）

# ==========================================
# モデル設定（二択）
# ==========================================
AVAILABLE_MODELS = [
    "gemini-3.5-flash-lite",  # デフォルト（高速）
    "gemini-3.6-flash",       # 標準（混雑時に 503 が出ることがあります）
]
DEFAULT_MODEL = AVAILABLE_MODELS[0]

# 画像縮小の上限（長辺ピクセル）。トークン節約と高速化のため
MAX_IMAGE_SIDE = 1280

# True: 起動時に、アドレスバーのない専用ウィンドウ（Edge）で解説画面を開く
# 開きたくないときは False にしてください（その場合は手動で http://localhost:5000 を開きます）
OPEN_APP_WINDOW = True
APP_WINDOW_SIZE = "480,900"


# ==========================================
# 🔑 設定の読み書き（config.json）
# ==========================================
def read_config():
    if os.path.exists(API_CONFIG_FILE):
        try:
            with open(API_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            print(f"[API] config.json の読み込みに失敗しました: {e}")
    return {}


def write_config(api_key, model):
    try:
        with open(API_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({'api_key': api_key, 'model': model}, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[API] config.json の保存に失敗しました: {e}")
        return False


def load_settings():
    """config.json からキーとモデルを読む。キーが無ければ入力を求めて保存する。"""
    cfg = read_config()
    key = str(cfg.get('api_key', '')).strip()
    model = cfg.get('model')
    if model not in AVAILABLE_MODELS:
        model = DEFAULT_MODEL

    if not key:
        print("==========================================")
        print(" 初回設定: Gemini API キーを貼り付けて Enter を押してください")
        print(" （黒い画面では、右クリックで貼り付けできます）")
        print("==========================================")
        key = input("APIキー: ").strip().strip('"').strip("'").strip()
        if not key:
            print("[API] キーが入力されませんでした。終了します。")
            input("Enter キーで閉じます")
            sys.exit(1)
        if write_config(key, model):
            print("[API] config.json に保存しました。次回から入力は不要です。")
            print("[API] キーを変えたいときは config.json を削除して、もう一度起動してください。")
    return key, model


GEMINI_API_KEY, selected_model = load_settings()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask(__name__)

# ★ 現在選択されている解析ターゲット（デフォルトは "left"）
current_target_side = "left"
side_lock = threading.Lock()

latest_result = {
    "title": "待機中（HVOS v0.7 / 【1】でシャッター）",
    "gemini_content": (
        "【HVOS v0.7 操作方法】\n"
        "1. 【←】または【→】キーを押して、解析対象（画面の「左の方」/「右の方」）を選択します。（現在：左の方）\n"
        "2. 配置が決まったら、キーボードの【1】キーを押してシャッターを切ります。\n\n"
        "※一度左右を設定すれば、変更しない限り【1】キーだけで繰り返し撮影できます。"
    ),
    "timestamp": ""
}
is_processing = False
processing_lock = threading.Lock()


def get_local_ip():
    """同じWi-Fi内のスマホからアクセスするためのローカルIPアドレスを取得"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


def generate_qr_code_base64(url):
    """スマホ接続用のQRコード画像を生成"""
    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')


def shrink_image(img):
    """長辺が MAX_IMAGE_SIDE を超える場合だけ縮小する（トークン節約・高速化）"""
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_IMAGE_SIDE:
        return img
    scale = MAX_IMAGE_SIDE / longest
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def set_target_side(side):
    """【←】/【→】キーで撮影ターゲット領域を切り替える"""
    global current_target_side
    with side_lock:
        current_target_side = side
        side_label = "左の方" if side == "left" else "右の方"
        print(f"[画面] 解析ターゲットを【画面の{side_label}】に設定しました。")

        # 解析中ではない時のみ待機メッセージを更新
        if not is_processing:
            latest_result["title"] = f"待機中（ターゲット：画面の{side_label}）"
            latest_result["gemini_content"] = (
                f"【解析ターゲット設定：画面の{side_label}】\n\n"
                f"キーボードの【1】を押すと、画面の{side_label}を撮影してAIガイド解析を開始します。\n"
                f"（※配置を変える場合は【←】または【→】を押してください）"
            )


def execute_analysis():
    """「1」キー押下時に選択中の領域（left / right）を撮影してGemini API解析を実行"""
    global is_processing

    with processing_lock:
        if is_processing:
            return
        is_processing = True

    with side_lock:
        side = current_target_side

    model_name = selected_model  # 処理開始時点で確定させる
    side_label = "左の方" if side == "left" else "右の方"
    print(f"[画面] シャッター実行: 画面{side_label}")

    latest_result["title"] = f"AI解析中（画面{side_label} / {model_name}）..."
    latest_result["gemini_content"] = (
        f"画面{side_label}を撮影し、Gemini ({model_name}) へ送信しています。\n"
        f"しばらくお待ちください。"
    )
    latest_result["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        # 1. 画面全体のスクリーンショットを取得
        screenshot = pyautogui.screenshot()
        screen_w, screen_h = screenshot.size

        # 2. 設定されたターゲット領域（左の方 1-49% / 右の方 51-99%）をトリミング
        top = int(screen_h * 0.08)
        bottom = int(screen_h * 0.98)
        if side == "left":
            left = int(screen_w * 0.01)
            right = int(screen_w * 0.49)
        else:
            left = int(screen_w * 0.51)
            right = int(screen_w * 0.99)

        # 3. 縮小してメモリ上で渡す（ディスクには保存しない）
        img = shrink_image(screenshot.crop((left, top, right, bottom)))
        print(f"[画面] 送信サイズ: {img.size[0]}x{img.size[1]}")

        prompt = (
            "あなたは最高のバーチャルツアーガイドです。この画面（Google Earthの3D景観またはストリートビュー）"
            "に写っている場所について、以下の構成で500文字程度で魅力的に解説してください。\n\n"
            "【場所の特定と概要】：ここがどこか、何という施設・自然・街並みか\n"
            "【歴史と背景】：この場所にまつわる歴史やストーリー、地理的な特徴など\n"
            "【ここだけの魅力・おすすめポイント】：訪れた人がワクワクする豆知識や見どころ\n"
            "【周囲のおすすめ・楽しみ方】：周辺の立ち寄りスポットや体験のポイント\n\n"
            "語り口は親しみやすく、聞いているだけで旅に出たくなるようなワクワクする文章でまとめてください。"
            "文末には必ず『（文字数：〇〇文字）』と実際に生成した文字数を記載してください。"
        )

        # 4. Gemini 呼び出し
        print(f"[API] 呼び出し実行モデル: {model_name}")
        response = gemini_client.models.generate_content(
            model=model_name,
            contents=[prompt, img]
        )

        latest_result["title"] = f"HVOS ガイド解説（{side_label} / {model_name}）"
        latest_result["gemini_content"] = response.text
        print("[API] 解析完了")

    except Exception as e:
        print(f"[API] エラー: {e}")
        latest_result["title"] = f"エラー発生（{model_name}）"
        latest_result["gemini_content"] = f"処理エラーが発生しました: {e}"

    finally:
        with processing_lock:
            is_processing = False


def trigger_shutter():
    threading.Thread(target=execute_analysis, daemon=True).start()

def trigger_set_left():
    set_target_side("left")

def trigger_set_right():
    set_target_side("right")


def open_app_window():
    """アドレスバーのない専用ウィンドウ（Edge のアプリモード）で解説画面を開く"""
    url = "http://localhost:5000"
    if os.name == 'nt':
        try:
            subprocess.Popen(
                f'start "" msedge --app={url} --window-size={APP_WINDOW_SIZE}', shell=True)
            print("[画面] 専用ウィンドウ（Edge）で解説画面を開きました")
            return
        except Exception as e:
            print(f"[画面] 専用ウィンドウを開けませんでした: {e}")
    webbrowser.open(url)


def start_keyboard_listener():
    """キーイベントのリスナー設定"""
    try:
        # シャッター（1キー / テンキー1）
        keyboard.add_hotkey('1', trigger_shutter)
        keyboard.add_hotkey('num 1', trigger_shutter)

        # ターゲット切り替え（矢印キー）
        keyboard.add_hotkey('left', trigger_set_left)
        keyboard.add_hotkey('right', trigger_set_right)
        print("[画面] キー監視を開始しました（【1】シャッター / 【←】【→】ターゲット切替）")
    except Exception as e:
        print(f"[画面] キーボードフック設定エラー: {e}")


# ==========================================
# Web UI (Flask) ルート設計
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HVOS</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Meiryo", sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 8px; }
        .bar { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
        .status { flex: 1; min-width: 0; font-size: 0.75rem; color: #94a3b8; line-height: 1.3; }
        .status .time { margin-left: 6px; color: #64748b; }
        .bar select { max-width: 46%; background-color: #2d3748; color: #ffffff; border: 1px solid #475569; border-radius: 4px; padding: 2px 4px; font-size: 0.72rem; }
        .card { background-color: #1e293b; border-radius: 8px; padding: 12px 14px; border-top: 3px solid #38bdf8; }
        .content { white-space: pre-wrap; line-height: 1.7; font-size: 0.95rem; color: #e2e8f0; }
        .info { margin-top: 10px; font-size: 0.75rem; color: #64748b; }
        .info summary { cursor: pointer; }
        .info .guide { margin: 8px 0; line-height: 1.5; color: #94a3b8; }
        .info .qr { text-align: center; margin-top: 8px; }
        .info .qr img { width: 110px; height: 110px; border-radius: 6px; }
    </style>
</head>
<body>
    <div class="bar">
        <div class="status"><span id="title">{{ result.title }}</span><span id="timestamp" class="time">{{ result.timestamp }}</span></div>
        <select id="model" title="使用モデル">
            {% for m in models %}
            <option value="{{ m }}" {% if m == model %}selected{% endif %}>{{ m }}</option>
            {% endfor %}
        </select>
    </div>

    <div class="card">
        <div id="content" class="content">{{ result.gemini_content }}</div>
    </div>

    <details class="info">
        <summary>接続情報（QRコード）・操作方法</summary>
        <div class="guide">
            【←】/【→】キー：撮影対象の変更（左の方 / 右の方）<br>
            【1】キー：シャッター（選択中の領域を撮影して解析）
        </div>
        <div class="qr">
            <img src="data:image/png;base64,{{ qr_base64 }}" alt="QR Code"><br>
            <span style="font-family:monospace; color:#38bdf8;">http://{{ local_ip }}:5000</span>
        </div>
    </details>

    <script>
        var sel = document.getElementById('model');

        function fetchLatestResult() {
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('title').innerText = data.title;
                    document.getElementById('timestamp').innerText = data.timestamp;
                    document.getElementById('content').innerText = data.gemini_content;
                    if (document.activeElement !== sel && sel.value !== data.model) {
                        sel.value = data.model;
                    }
                });
        }
        setInterval(fetchLatestResult, 1000);

        sel.addEventListener('change', function () {
            fetch('/api/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: sel.value })
            });
        });
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    local_ip = get_local_ip()
    phone_url = f"http://{local_ip}:5000"
    qr_base64 = generate_qr_code_base64(phone_url)
    return render_template_string(
        HTML_TEMPLATE,
        result=latest_result,
        local_ip=local_ip,
        qr_base64=qr_base64,
        models=AVAILABLE_MODELS,
        model=selected_model,
    )


@app.route('/api/data')
@app.route('/api/result')
def get_result():
    data = dict(latest_result)
    data["model"] = selected_model
    return jsonify(data)


@app.route('/api/model', methods=['POST'])
def set_model():
    """ブラウザのドロップダウンからモデルを切り替える（候補にあるものだけ受け付ける）"""
    global selected_model
    payload = request.get_json(silent=True) or {}
    new_model = payload.get("model")
    if new_model not in AVAILABLE_MODELS:
        return jsonify({"ok": False, "error": "unknown model"}), 400
    selected_model = new_model
    write_config(GEMINI_API_KEY, selected_model)
    print(f"[API] 使用モデル変更 ➔ 【{selected_model}】")
    return jsonify({"ok": True, "model": selected_model})


# ==========================================
# メイン実行処理
# ==========================================
if __name__ == '__main__':
    local_ip = get_local_ip()
    print("=" * 60)
    print("  HVOS PC Browser Base Edition v0.7 を起動しました")
    print(f"  PC用アドレス     : http://localhost:5000")
    print(f"  スマホ用アドレス : http://{local_ip}:5000")
    print(f"  使用モデル       : {selected_model}")
    print("=" * 60)
    print("  [操作方法] 【←/→】で左右エリア選択、【1】でシャッター撮影")

    start_keyboard_listener()
    if OPEN_APP_WINDOW:
        threading.Timer(2.0, open_app_window).start()
    app.run(host='0.0.0.0', port=5000, debug=False)
