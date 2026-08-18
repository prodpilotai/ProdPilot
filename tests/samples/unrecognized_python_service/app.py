from flask import Flask

app = Flask(__name__)


@app.get("/reports")
def reports():
    return {"reports": []}
