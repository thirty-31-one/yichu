import json
import os
import uuid
import base64
import requests
from datetime import datetime
from io import BytesIO
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory, render_template

app = Flask(__name__)

DATA_FILE = "data/wardrobe.json"
CONFIG_FILE = "data/config.json"
IMAGE_DIR = "images"

CATEGORIES = ["上衣", "裤子", "裙子", "帽子", "鞋子", "内衣", "配饰"]

os.makedirs(IMAGE_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)


# ── Data helpers ───────────────────────────────────────
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"items": [], "outfits": [], "photos": []}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"api_key": ""}


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f)


# ── Static files ───────────────────────────────────────
@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGE_DIR, filename)


# ── Config ─────────────────────────────────────────────
@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        cfg = request.get_json()
        save_config(cfg)
        return jsonify({"ok": True})
    return jsonify(load_config())


# ── Items ──────────────────────────────────────────────
@app.route("/api/items")
def list_items():
    data = load_data()
    cat = request.args.get("category")
    items = data["items"]
    if cat and cat != "全部":
        items = [i for i in items if i["category"] == cat]
    return jsonify(items)


@app.route("/api/items", methods=["POST"])
def create_item():
    name = request.form.get("name", "")
    category = request.form.get("category", "")
    color = request.form.get("color", "")
    season = request.form.get("season", "")
    file = request.files.get("image")

    if not name or not file:
        return jsonify({"error": "名称和照片必填"}), 400

    ext = file.filename.rsplit(".", 1)[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(IMAGE_DIR, filename)
    img = Image.open(file.stream)
    img.save(filepath)

    data = load_data()
    item = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "category": category,
        "color": color,
        "season": season,
        "image": filename,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    data["items"].append(item)
    save_data(data)
    return jsonify(item), 201


@app.route("/api/items/<item_id>", methods=["DELETE"])
def delete_item(item_id):
    data = load_data()
    data["items"] = [i for i in data["items"] if i["id"] != item_id]
    save_data(data)
    return jsonify({"ok": True})


# ── Photos ─────────────────────────────────────────────
@app.route("/api/photos")
def list_photos():
    return jsonify(load_data().get("photos", []))


@app.route("/api/photos", methods=["POST"])
def create_photo():
    name = request.form.get("name", "")
    file = request.files.get("image")
    if not name or not file:
        return jsonify({"error": "名称和照片必填"}), 400

    ext = file.filename.rsplit(".", 1)[-1]
    filename = f"photo_{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(IMAGE_DIR, filename)
    img = Image.open(file.stream)
    img.save(filepath)

    data = load_data()
    photo = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "image": filename,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    data.setdefault("photos", []).append(photo)
    save_data(data)
    return jsonify(photo), 201


@app.route("/api/photos/<photo_id>", methods=["DELETE"])
def delete_photo(photo_id):
    data = load_data()
    data["photos"] = [p for p in data.get("photos", []) if p["id"] != photo_id]
    save_data(data)
    return jsonify({"ok": True})


# ── Outfits ────────────────────────────────────────────
@app.route("/api/outfits")
def list_outfits():
    data = load_data()
    id_map = {i["id"]: i for i in data["items"]}
    result = []
    for o in reversed(data.get("outfits", [])):
        result.append({
            "id": o["id"],
            "name": o["name"],
            "created_at": o["created_at"],
            "items": [id_map[iid] for iid in o["items"] if iid in id_map],
        })
    return jsonify(result)


@app.route("/api/outfits", methods=["POST"])
def create_outfit():
    body = request.get_json()
    data = load_data()
    data.setdefault("outfits", []).append({
        "id": uuid.uuid4().hex[:8],
        "name": body.get("name", ""),
        "items": body.get("items", []),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_data(data)
    return jsonify({"ok": True}), 201


@app.route("/api/outfits/<outfit_id>", methods=["DELETE"])
def delete_outfit(outfit_id):
    data = load_data()
    data["outfits"] = [o for o in data.get("outfits", []) if o["id"] != outfit_id]
    save_data(data)
    return jsonify({"ok": True})


# ── AI Try-on ──────────────────────────────────────────
def composite_reference(body_path, clothing_paths):
    body_img = Image.open(body_path).convert("RGB")
    bh = 1024
    bw = int(body_img.width * bh / body_img.height)
    body_img = body_img.resize((bw, bh), Image.LANCZOS)

    clothes = []
    for cp in clothing_paths:
        cimg = Image.open(cp).convert("RGB")
        cimg = cimg.resize((int(cimg.width * bh / cimg.height), bh), Image.LANCZOS)
        clothes.append(cimg)

    total_w = bw + sum(c.width for c in clothes) + 10 * len(clothes)
    canvas = Image.new("RGB", (total_w, bh), (255, 255, 255))
    canvas.paste(body_img, (0, 0))
    x = bw + 10
    for c in clothes:
        canvas.paste(c, (x, 0))
        x += c.width + 10

    buf = BytesIO()
    canvas.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@app.route("/api/tryon", methods=["POST"])
def api_tryon():
    body = request.get_json()
    api_key = body.get("api_key", "")
    if not api_key:
        return jsonify({"error": "请先设置API Key"}), 400

    try:
        from volcenginesdkarkruntime import Ark
    except ImportError:
        return jsonify({"error": "服务器未安装SDK"}), 500

    data = load_data()
    id_map = {i["id"]: i for i in data["items"]}
    photo_map = {p["id"]: p for p in data.get("photos", [])}

    photo_id = body.get("photo_id")
    item_ids = body.get("item_ids", [])

    if not photo_id or not item_ids:
        return jsonify({"error": "请选择全身照和至少一件衣物"}), 400

    photo = photo_map.get(photo_id)
    items = [id_map[iid] for iid in item_ids if iid in id_map]

    if not photo or not items:
        return jsonify({"error": "未找到照片或衣物"}), 400

    body_path = os.path.join(IMAGE_DIR, photo["image"])
    clothing_paths = [os.path.join(IMAGE_DIR, it["image"]) for it in items]
    clothing_desc = "、".join(f"{it['name']}({it['category']})" for it in items)

    ref_b64 = composite_reference(body_path, clothing_paths)

    prompt = (
        f"虚拟试衣：让照片中的人物穿上以下衣物——{clothing_desc}。"
        "保持人物的姿势、面部、发型、肤色和照片背景完全不变。"
        "衣物应自然贴合身体，符合真实的光影和褶皱效果。"
        "写实摄影风格，高质量，不做任何面部或背景修改。"
    )

    client = Ark(
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key=api_key,
    )

    try:
        resp = client.images.generate(
            model="doubao-seedream-5-0-260128",
            prompt=prompt,
            image=[f"data:image/jpeg;base64,{ref_b64}"],
            size="2K",
            watermark=False,
        )
    except Exception as e:
        return jsonify({"error": f"API调用失败: {e}"}), 500

    if resp.data and resp.data[0].url:
        # Download result
        dl = requests.get(resp.data[0].url, timeout=30)
        filename = f"tryon_{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(IMAGE_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(dl.content)
        return jsonify({"url": f"/images/{filename}"})

    return jsonify({"error": "API返回为空"}), 500


# ── Frontend ───────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", categories=CATEGORIES)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8501, debug=True)
