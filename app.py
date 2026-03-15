from flask import Flask, request, jsonify, render_template
import pandas as pd
import time
from difflib import SequenceMatcher

app = Flask(__name__)

Sheet_ID = "1eX0HjdZKYD9TvvavRWzL1uQ0sCFv_u_X-38vNholUeA"

cache_links = {}
last_update = 0
CACHE_TIME = 28800  # 8 hours 


def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def load_links():

    global cache_links, last_update

    if cache_links and time.time() - last_update < CACHE_TIME:
        return cache_links

    url = f"https://docs.google.com/spreadsheets/d/{Sheet_ID}/export?format=csv"
    df = pd.read_csv(url)

    links_dict = {}

    for _, row in df.iterrows():

        if pd.isna(row["keywords"]) or pd.isna(row["link"]):
            continue

        keywords = str(row["keywords"]).lower().split(",")

        for keyword in keywords:
            links_dict[keyword.strip()] = row["link"]

    cache_links = links_dict
    last_update = time.time()

    return cache_links


@app.route('/')
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():

    user_message = request.json["message"].lower()

    links = load_links()

    best_score = 0
    best_link = ""

    for keyword, link in links.items():

        score = similarity(user_message, keyword)

        if score > best_score:
            best_score = score
            best_link = link

    if best_score > 0.4:
        return jsonify({"reply": best_link})

    return jsonify({"reply": "لم أجد رابط مناسب. حاول كتابة السؤال بطريقة مختلفة."})


if __name__ == "__main__":
    app.run()
