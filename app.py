from flask import Flask, request, jsonify, render_template
import pandas as pd
app = Flask(__name__)

Sheet_ID = "1eX0HjdZKYD9TvvavRWzL1uQ0sCFv_u_X-38vNholUeA"

def load_links():
    url = f"https://docs.google.com/spreadsheets/d/{Sheet_ID}/export?format=csv"
    df = pd.read_csv(url)
    return df
df=load_links()
@app.route('/')
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json["message"].lower()
    df=load_links()
    for _, row in df.iterrows():
        keywords = str(row["keywords"]).lower().split(",")
        for keyword in keywords:
            if keyword.strip() in user_message:
                return jsonify({"reply": row["link"]})

    return jsonify({"reply": "لم أجد رابط مناسبا لرسالتك. حاول استخدام كلمات أخرى."})

if __name__ == "__main__":

    app.run(debug=True)
