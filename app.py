import os
import json
import sqlite3
import csv
from datetime import datetime, date
from pathlib import Path
from functools import wraps

import cv2
import numpy as np
import pandas as pd
import face_recognition
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# Define Base Paths to avoid "File Not Found" errors
BASE_DIR = Path(__file__).resolve().parent
TRAINING_DIR = BASE_DIR / "Training images"
ATTENDANCE_CSV = BASE_DIR / "attendance.csv"
DB_PATH = BASE_DIR / "information.db"
CRED_CSV = BASE_DIR / "cred.csv"


def norm_name(s: str) -> str:
    return " ".join((s or "").strip().upper().split())


def ensure_files():
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    if not ATTENDANCE_CSV.exists():
        ATTENDANCE_CSV.write_text("Name,Roll,Time\n", encoding="utf-8")
    
    # Ensure cred.csv exists - credentials should be set up manually
    if not CRED_CSV.exists():
        with open(CRED_CSV, 'w', encoding='utf-8') as f:
            f.write("username,password\n")


def ensure_db():
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS Students (Name TEXT PRIMARY KEY, RollNo TEXT NOT NULL)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS Attendance
               (NAME TEXT NOT NULL,
                RollNo TEXT,
                Time TEXT NOT NULL,
                Date TEXT NOT NULL)"""
        )
        try:
            conn.execute("ALTER TABLE Attendance ADD COLUMN RollNo TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


ensure_files()
ensure_db()


# --- DECORATORS ---
def login_required(f):
    """
    A decorator to ensure the user is logged in before accessing a route.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("how")) 
        return f(*args, **kwargs)
    return decorated_function


def get_rollno(conn: sqlite3.Connection, person_name: str) -> str | None:
    try:
        cur = conn.execute("SELECT RollNo FROM Students WHERE Name=?", (person_name,))
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


@app.route("/new", methods=["GET", "POST"])
@login_required 
def new_student():
    if request.method == "GET":
        return render_template("new.html")

    student_id = (request.form.get("student_id") or "").strip()
    student_name = (request.form.get("student_name") or "").strip()

    if not student_id or not student_name:
        flash("Please provide both Student ID and Student Name.", "error")
        return redirect(url_for("new_student"))

    flash("Please use the registration form to capture image (POST to /name).", "error")
    return redirect(url_for("new_student"))


@app.route("/name", methods=["GET", "POST"])
@login_required 
def name():
    if request.method != "POST":
        return "All is not well"

    raw_name = request.form.get("name1", "")
    raw_reg_id = request.form.get("name2", "")

    person_name = norm_name(raw_name)
    roll_no = (raw_reg_id or "").strip()

    if not person_name:
        return "Missing name1", 400
    if not roll_no:
        return "Missing registration id (roll number)", 400

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS Students (Name TEXT PRIMARY KEY, RollNo TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO Students (Name, RollNo) VALUES (?, ?)",
            (person_name, roll_no),
        )
        conn.commit()
    finally:
        conn.close()

    cam = cv2.VideoCapture(0)
    try:
        while True:
            ret, frame = cam.read()
            if not ret:
                return "failed to grab frame", 500

            cv2.imshow("Press Space to capture image", frame)
            k = cv2.waitKey(1)

            if k % 256 == 27:  # ESC
                break
            elif k % 256 == 32:  # SPACE
                img_name = f"{person_name}.png"
                out_path = TRAINING_DIR / img_name
                cv2.imwrite(str(out_path), frame)
                break
    finally:
        cam.release()
        cv2.destroyAllWindows()

    return render_template("image.html")


@app.route("/", methods=["GET", "POST"])
def recognize():
    if request.method != "POST":
        return render_template("main.html")

    image_paths = [p for p in TRAINING_DIR.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    if not image_paths:
        return f"No training images found in: {TRAINING_DIR}", 500

    images = []
    classNames = []
    for p in image_paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        images.append(img)
        classNames.append(norm_name(p.stem))

    def findEncodings(images_):
        encodeList = []
        validNames = []
        for img, nm in zip(images_, classNames):
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            encs = face_recognition.face_encodings(rgb)
            if not encs:
                continue
            encodeList.append(encs[0])
            validNames.append(nm)
        return encodeList, validNames

    def markData(person_name: str):
        person_name = norm_name(person_name)
        now = datetime.now()
        tm = now.strftime("%H:%M")
        today = str(date.today())

        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS Attendance
                   (NAME TEXT NOT NULL,
                    RollNo TEXT,
                    Time TEXT NOT NULL,
                    Date TEXT NOT NULL)"""
            )
            roll = get_rollno(conn, person_name) or "Unknown"
            conn.execute(
                "INSERT INTO Attendance (NAME, RollNo, Time, Date) VALUES (?, ?, ?, ?)",
                (person_name, roll, tm, today),
            )
            conn.commit()
        finally:
            conn.close()

    def markAttendanceCSV(person_name: str):
        person_name = norm_name(person_name)
        ensure_files()

        conn = sqlite3.connect(str(DB_PATH))
        try:
            roll = get_rollno(conn, person_name) or ""
        finally:
            conn.close()

        with ATTENDANCE_CSV.open("r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        existing_names = set()
        for line in lines[1:]:
            parts = [p.strip() for p in line.strip().split(",")]
            if parts and parts[0]:
                existing_names.add(parts[0])

        if person_name not in existing_names:
            tm = datetime.now().strftime("%H:%M")
            with ATTENDANCE_CSV.open("a", encoding="utf-8") as f:
                f.write(f"{person_name},{roll},{tm}\n")

    encodeListKnown, validNames = findEncodings(images)
    if not encodeListKnown:
        return "No encodable faces found in training images.", 500

    cap = cv2.VideoCapture(0)
    try:
        while True:
            success, img = cap.read()
            if not success:
                break

            imgS = cv2.resize(img, (0, 0), None, 0.25, 0.25)
            imgS = cv2.cvtColor(imgS, cv2.COLOR_BGR2RGB)

            facesCurFrame = face_recognition.face_locations(imgS)
            encodesCurFrame = face_recognition.face_encodings(imgS, facesCurFrame)

            for encodeFace, faceLoc in zip(encodesCurFrame, facesCurFrame):
                faceDis = face_recognition.face_distance(encodeListKnown, encodeFace)
                matchIndex = int(np.argmin(faceDis))

                if faceDis[matchIndex] < 0.50:
                    person_name = validNames[matchIndex]
                    markAttendanceCSV(person_name)
                    markData(person_name)
                    display_name = person_name
                else:
                    display_name = "Unknown"

                y1, x2, y2, x1 = faceLoc
                y1, x2, y2, x1 = y1 * 4, x2 * 4, y2 * 4, x1 * 4
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.rectangle(img, (x1, y2 - 35), (x2, y2), (0, 255, 0), cv2.FILLED)
                cv2.putText(img, display_name, (x1 + 6, y2 - 6),
                            cv2.FONT_HERSHEY_COMPLEX, 1, (255, 255, 255), 2)

            cv2.imshow("Punch your Attendance", img)
            if cv2.waitKey(1) == 27:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return render_template("first.html")


@app.route("/login", methods=["POST"])
def login():
    # 1. Try getting data from Form (Browser)
    input_user = request.form.get("username", "").strip().lower()
    input_pass = request.form.get("password", "").strip()

    # 2. If empty, try getting data from JSON (API/JS)
    if not input_user:
        try:
            json_data = request.get_json(force=True, silent=True)
            if json_data:
                input_user = str(json_data.get("username", "")).strip().lower()
                input_pass = str(json_data.get("password", "")).strip()
        except Exception:
            pass

    print(f"DEBUG: Attempting login for '{input_user}'")

    if not input_user or not input_pass:
        # If accessed via browser, show error
        if request.form:
            flash("Missing username or password", "error")
            return redirect(url_for("how"))
        return "failed"

    # Hardcoded emergency fallback
    if not CRED_CSV.exists():
        if request.form:
            flash("Credential file missing", "error")
            return redirect(url_for("how"))
        return "failed"

    try:
        with open(CRED_CSV, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            # Normalize headers
            if reader.fieldnames:
                reader.fieldnames = [fn.strip().lower() for fn in reader.fieldnames]

            for row in reader:
                stored_user = str(row.get('username', '')).strip().lower()
                stored_pass = str(row.get('password', '')).strip()
                
                if stored_user == input_user and stored_pass == input_pass:
                    session["username"] = input_user
                    if request.form:
                        return redirect(url_for("dashboard"))
                    return "success"
    except Exception as e:
        print(f"DEBUG: CSV Error: {e}")

    # Login failed
    if request.form:
        flash("Invalid Username or Password", "error")
        return redirect(url_for("how"))
    
    return "failed"


@app.route("/checklogin")
def checklogin():
    is_logged_in = "username" in session
    return jsonify({"logged_in": is_logged_in, "user": session.get("username")})


@app.route("/how", methods=["GET", "POST"])
def how():
    return render_template("form.html")


@app.route("/data", methods=["GET", "POST"])
@login_required 
def data():
    if request.method == "POST":
        today = str(date.today())
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT NAME, RollNo, Time, Date FROM Attendance WHERE Date=?", (today,))
        rows = cur.fetchall()
        conn.close()
        return render_template("form2.html", rows=rows)

    return render_template("form1.html")


@app.route("/whole", methods=["GET", "POST"])
@login_required 
def whole():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT NAME, RollNo, Time, Date FROM Attendance")
    rows = cur.fetchall()
    conn.close()
    return render_template("form3.html", rows=rows)


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    today = str(date.today())
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT NAME, RollNo, Time, Date FROM Attendance WHERE Date=?",
            (today,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return render_template("form2.html", rows=rows)


if __name__ == "__main__":
    app.run(debug=True)