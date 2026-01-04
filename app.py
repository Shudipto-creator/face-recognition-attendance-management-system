import os
import json
import sqlite3
import csv
from datetime import datetime, date
from io import BytesIO
from pathlib import Path
from functools import wraps
from typing import Optional, List

import cv2
import numpy as np
import pandas as pd
import face_recognition
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify, send_file, get_flashed_messages

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

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


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    out = []
    prev_us = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append("_")
                prev_us = True
    res = "".join(out).strip("_")
    return res or "default"


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_files():
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    if not ATTENDANCE_CSV.exists():
        ATTENDANCE_CSV.write_text("Name,Roll,Time\n", encoding="utf-8")
    
    if not CRED_CSV.exists():
        with open(CRED_CSV, 'w', encoding='utf-8') as f:
            f.write("username,password\n")


def ensure_db():
    conn = connect_db()
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

        conn.execute(
            """CREATE TABLE IF NOT EXISTS Departments (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   Name TEXT NOT NULL UNIQUE,
                   Slug TEXT NOT NULL UNIQUE
               )"""
        )

        conn.execute(
            """CREATE TABLE IF NOT EXISTS Majors (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   DepartmentId INTEGER NOT NULL,
                   Name TEXT NOT NULL,
                   Slug TEXT NOT NULL,
                   UNIQUE(DepartmentId, Name),
                   FOREIGN KEY(DepartmentId) REFERENCES Departments(Id) ON DELETE CASCADE
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS Courses (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   DepartmentId INTEGER NOT NULL,
                   MajorId INTEGER,
                   Name TEXT NOT NULL,
                   Slug TEXT NOT NULL,
                   UNIQUE(DepartmentId, Name),
                   FOREIGN KEY(DepartmentId) REFERENCES Departments(Id) ON DELETE CASCADE,
                   FOREIGN KEY(MajorId) REFERENCES Majors(Id) ON DELETE SET NULL
               )"""
        )

        try:
            conn.execute("ALTER TABLE Courses ADD COLUMN MajorId INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """CREATE TABLE IF NOT EXISTS StudentsV2 (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   DepartmentId INTEGER NOT NULL,
                   Name TEXT NOT NULL,
                   RollNo TEXT NOT NULL,
                   UNIQUE(DepartmentId, RollNo),
                   FOREIGN KEY(DepartmentId) REFERENCES Departments(Id) ON DELETE CASCADE
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS Enrollments (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   StudentId INTEGER NOT NULL,
                   CourseId INTEGER NOT NULL,
                   UNIQUE(StudentId, CourseId),
                   FOREIGN KEY(StudentId) REFERENCES StudentsV2(Id) ON DELETE CASCADE,
                   FOREIGN KEY(CourseId) REFERENCES Courses(Id) ON DELETE CASCADE
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS AttendanceV2 (
                   Id INTEGER PRIMARY KEY AUTOINCREMENT,
                   StudentId INTEGER NOT NULL,
                   CourseId INTEGER NOT NULL,
                   Time TEXT NOT NULL,
                   Date TEXT NOT NULL,
                   UNIQUE(StudentId, CourseId, Date),
                   FOREIGN KEY(StudentId) REFERENCES StudentsV2(Id) ON DELETE CASCADE,
                   FOREIGN KEY(CourseId) REFERENCES Courses(Id) ON DELETE CASCADE
               )"""
        )

        cur = conn.execute("SELECT COUNT(1) AS Cnt FROM Departments")
        if int(cur.fetchone()["Cnt"]) == 0:
            dept_name = "General"
            dept_slug = slugify(dept_name)
            conn.execute("INSERT INTO Departments (Name, Slug) VALUES (?, ?)", (dept_name, dept_slug))
            dept_id = int(conn.execute("SELECT Id FROM Departments WHERE Slug=?", (dept_slug,)).fetchone()["Id"])
            major_name = "General"
            major_slug = slugify(major_name)
            conn.execute(
                "INSERT INTO Majors (DepartmentId, Name, Slug) VALUES (?, ?, ?)",
                (dept_id, major_name, major_slug),
            )
            major_id = int(
                conn.execute(
                    "SELECT Id FROM Majors WHERE DepartmentId=? AND Slug=?",
                    (dept_id, major_slug),
                ).fetchone()["Id"]
            )
            course_name = "General"
            course_slug = slugify(course_name)
            conn.execute(
                "INSERT INTO Courses (DepartmentId, MajorId, Name, Slug) VALUES (?, ?, ?, ?)",
                (dept_id, major_id, course_name, course_slug),
            )

        dept_rows = conn.execute("SELECT Id FROM Departments").fetchall()
        for d in dept_rows:
            dept_id = int(d["Id"])
            mrow = conn.execute(
                "SELECT Id FROM Majors WHERE DepartmentId=? ORDER BY Id ASC LIMIT 1",
                (dept_id,),
            ).fetchone()
            if not mrow:
                major_name = "General"
                major_slug = slugify(major_name)
                conn.execute(
                    "INSERT OR IGNORE INTO Majors (DepartmentId, Name, Slug) VALUES (?, ?, ?)",
                    (dept_id, major_name, major_slug),
                )
                mrow = conn.execute(
                    "SELECT Id FROM Majors WHERE DepartmentId=? AND Slug=?",
                    (dept_id, major_slug),
                ).fetchone()
            default_major_id = int(mrow["Id"]) if mrow else None
            if default_major_id is not None:
                conn.execute(
                    "UPDATE Courses SET MajorId=? WHERE DepartmentId=? AND (MajorId IS NULL OR MajorId=0)",
                    (default_major_id, dept_id),
                )

        legacy_cnt = int(conn.execute("SELECT COUNT(1) AS Cnt FROM Students").fetchone()["Cnt"])
        v2_cnt = int(conn.execute("SELECT COUNT(1) AS Cnt FROM StudentsV2").fetchone()["Cnt"])
        if legacy_cnt > 0 and v2_cnt == 0:
            dept_id = int(conn.execute("SELECT Id FROM Departments ORDER BY Id ASC LIMIT 1").fetchone()["Id"])
            course_id = int(conn.execute("SELECT Id FROM Courses ORDER BY Id ASC LIMIT 1").fetchone()["Id"])

            cur = conn.execute("SELECT Name, RollNo FROM Students")
            for r in cur.fetchall():
                nm = norm_name(r["Name"])
                roll = str(r["RollNo"] or "").strip()
                if not nm or not roll:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO StudentsV2 (DepartmentId, Name, RollNo) VALUES (?, ?, ?)",
                    (dept_id, nm, roll),
                )

            cur = conn.execute("SELECT Id FROM StudentsV2")
            for r in cur.fetchall():
                conn.execute(
                    "INSERT OR IGNORE INTO Enrollments (StudentId, CourseId) VALUES (?, ?)",
                    (int(r["Id"]), course_id),
                )

            try:
                att_rows = conn.execute("SELECT NAME, Time, Date FROM Attendance").fetchall()
            except sqlite3.OperationalError:
                att_rows = []

            for a in att_rows:
                nm = norm_name(a["NAME"])
                sid_row = conn.execute(
                    "SELECT Id FROM StudentsV2 WHERE DepartmentId=? AND Name=?",
                    (dept_id, nm),
                ).fetchone()
                if not sid_row:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO AttendanceV2 (StudentId, CourseId, Time, Date) VALUES (?, ?, ?, ?)",
                    (int(sid_row["Id"]), course_id, str(a["Time"]), str(a["Date"])),
                )
        conn.commit()
    finally:
        conn.close()


ensure_files()
ensure_db()


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


def get_rollno(conn: sqlite3.Connection, person_name: str) -> Optional[str]:
    try:
        cur = conn.execute("SELECT RollNo FROM Students WHERE Name=?", (person_name,))
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def get_student_for_course(conn: sqlite3.Connection, student_id: int, course_id: int) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        """SELECT s.Id, s.Name, s.RollNo,
                  d.Name AS DepartmentName,
                  m.Name AS MajorName,
                  c.Name AS CourseName
           FROM StudentsV2 s
           JOIN Departments d ON d.Id = s.DepartmentId
           JOIN Enrollments e ON e.StudentId = s.Id
           JOIN Courses c ON c.Id = e.CourseId
           LEFT JOIN Majors m ON m.Id = c.MajorId
           WHERE s.Id=? AND e.CourseId=?""",
        (student_id, course_id),
    )
    return cur.fetchone()


def list_departments(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT Id, Name FROM Departments ORDER BY Name ASC").fetchall())


def list_courses(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT c.Id,
                      c.Name,
                      c.DepartmentId,
                      d.Name AS DepartmentName,
                      c.MajorId,
                      m.Name AS MajorName
               FROM Courses c
               JOIN Departments d ON d.Id=c.DepartmentId
               LEFT JOIN Majors m ON m.Id=c.MajorId
               ORDER BY d.Name ASC, m.Name ASC, c.Name ASC"""
        ).fetchall()
    )


def list_majors(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT m.Id,
                      m.Name,
                      m.DepartmentId,
                      d.Name AS DepartmentName
               FROM Majors m
               JOIN Departments d ON d.Id=m.DepartmentId
               ORDER BY d.Name ASC, m.Name ASC"""
        ).fetchall()
    )


def course_training_dir(conn: sqlite3.Connection, course_id: int) -> Path:
    row = conn.execute(
        """SELECT c.Slug AS CourseSlug,
                  d.Slug AS DepartmentSlug,
                  m.Slug AS MajorSlug
           FROM Courses c
           JOIN Departments d ON d.Id=c.DepartmentId
           LEFT JOIN Majors m ON m.Id=c.MajorId
           WHERE c.Id=?""",
        (course_id,),
    ).fetchone()
    if not row:
        return TRAINING_DIR
    major_slug = str(row["MajorSlug"] or "general")
    return TRAINING_DIR / str(row["DepartmentSlug"]) / major_slug / str(row["CourseSlug"])


@app.route("/new", methods=["GET", "POST"])
@login_required 
def new_student():
    if request.method == "GET":
        conn = connect_db()
        try:
            departments = list_departments(conn)
            majors = list_majors(conn)
            courses = list_courses(conn)
        finally:
            conn.close()
        return render_template("new.html", departments=departments, majors=majors, courses=courses)

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
    course_id_raw = (request.form.get("course_id") or "").strip()
    dept_id_raw = (request.form.get("department_id") or "").strip()
    major_id_raw = (request.form.get("major_id") or "").strip()

    person_name = norm_name(raw_name)
    roll_no = (raw_reg_id or "").strip()
    try:
        course_id = int(course_id_raw)
        department_id = int(dept_id_raw)
        major_id = int(major_id_raw)
    except ValueError:
        flash("Please select a School, Major and Course.", "error")
        return redirect(url_for("new_student"))

    if not person_name:
        return "Missing name1", 400
    if not roll_no:
        return "Missing registration id (roll number)", 400

    conn = connect_db()
    try:
        course_row = conn.execute(
            "SELECT Id, DepartmentId, MajorId FROM Courses WHERE Id=?",
            (course_id,),
        ).fetchone()
        if (
            (not course_row)
            or int(course_row["DepartmentId"]) != department_id
            or int(course_row["MajorId"] or 0) != major_id
        ):
            flash("Invalid School/Major/Course selection.", "error")
            return redirect(url_for("new_student"))

        conn.execute(
            "INSERT OR IGNORE INTO StudentsV2 (DepartmentId, Name, RollNo) VALUES (?, ?, ?)",
            (department_id, person_name, roll_no),
        )
        conn.execute(
            "UPDATE StudentsV2 SET Name=? WHERE DepartmentId=? AND RollNo=?",
            (person_name, department_id, roll_no),
        )
        student_id = int(
            conn.execute(
                "SELECT Id FROM StudentsV2 WHERE DepartmentId=? AND RollNo=?",
                (department_id, roll_no),
            ).fetchone()["Id"]
        )
        conn.execute(
            "INSERT OR IGNORE INTO Enrollments (StudentId, CourseId) VALUES (?, ?)",
            (student_id, course_id),
        )
        conn.commit()
    finally:
        conn.close()

    conn2 = connect_db()
    try:
        out_dir = course_training_dir(conn2, course_id)
    finally:
        conn2.close()

    out_dir.mkdir(parents=True, exist_ok=True)

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
                img_name = f"student_{student_id}.png"
                out_path = out_dir / img_name
                cv2.imwrite(str(out_path), frame)
                break
    finally:
        cam.release()
        cv2.destroyAllWindows()

    return render_template("image.html")


@app.route("/", methods=["GET", "POST"])
def recognize():
    if request.method != "POST":
        conn = connect_db()
        try:
            departments = list_departments(conn)
            majors = list_majors(conn)
            courses = list_courses(conn)
        finally:
            conn.close()
        return render_template("main.html", departments=departments, majors=majors, courses=courses)

    course_id_raw = (request.form.get("course_id") or "").strip()
    dept_id_raw = (request.form.get("department_id") or "").strip()
    major_id_raw = (request.form.get("major_id") or "").strip()
    try:
        course_id = int(course_id_raw)
        department_id = int(dept_id_raw)
        major_id = int(major_id_raw)
    except ValueError:
        flash("Please select a School, Major and Course first.", "error")
        return redirect(url_for("recognize"))

    conn = connect_db()
    try:
        course_row = conn.execute(
            "SELECT Id, DepartmentId, MajorId FROM Courses WHERE Id=?",
            (course_id,),
        ).fetchone()
        if (
            (not course_row)
            or int(course_row["DepartmentId"]) != department_id
            or int(course_row["MajorId"] or 0) != major_id
        ):
            flash("Invalid School/Major/Course selection.", "error")
            return redirect(url_for("recognize"))
        training_dir = course_training_dir(conn, course_id)
    finally:
        conn.close()

    image_paths = []
    if training_dir.exists():
        image_paths.extend([p for p in training_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")])

    if not image_paths:
        image_paths = [p for p in TRAINING_DIR.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")]

    if not image_paths:
        return f"No training images found in: {training_dir}", 500

    images = []
    classNames = []
    for p in image_paths:
        img = cv2.imread(str(p))
        if img is None:
            continue
        stem = p.stem
        sid = None  # type: Optional[int]
        if stem.startswith("student_"):
            try:
                sid = int(stem.split("_", 1)[1])
            except ValueError:
                sid = None

        conn = connect_db()
        try:
            if sid is None:
                nm = norm_name(stem)
                sid_row = conn.execute(
                    """SELECT s.Id
                       FROM StudentsV2 s
                       JOIN Enrollments e ON e.StudentId=s.Id
                       WHERE e.CourseId=? AND s.Name=?
                       ORDER BY s.Id ASC
                       LIMIT 1""",
                    (course_id, nm),
                ).fetchone()
                if sid_row:
                    sid = int(sid_row["Id"])

            st = get_student_for_course(conn, sid, course_id) if sid is not None else None
        finally:
            conn.close()
        if not st:
            continue
        images.append(img)
        classNames.append(str(st["Id"]))

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

    def markData(student_id_str: str):
        try:
            sid = int(student_id_str)
        except ValueError:
            return False
        now = datetime.now()
        tm = now.strftime("%H:%M")
        today = str(date.today())

        conn = connect_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) as count FROM AttendanceV2 WHERE StudentId=? AND CourseId=? AND Date=?",
                (sid, course_id, today)
            )
            existing = cur.fetchone()[0]

            if existing > 0:
                return False

            conn.execute(
                "INSERT OR IGNORE INTO AttendanceV2 (StudentId, CourseId, Time, Date) VALUES (?, ?, ?, ?)",
                (sid, course_id, tm, today),
            )
            conn.commit()
            print(f"✓ Attendance marked for student {sid} at {tm}")
            return True
        finally:
            conn.close()

    def markAttendanceCSV(student_id_str: str):
        try:
            sid = int(student_id_str)
        except ValueError:
            return False
        ensure_files()
        conn = connect_db()
        try:
            st = get_student_for_course(conn, sid, course_id)
        finally:
            conn.close()
        if not st:
            return False
        person_name = norm_name(str(st["Name"]))
        roll = str(st["RollNo"] or "")

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
            return True
        return False

    encodeListKnown, validNames = findEncodings(images)
    if not encodeListKnown:
        return "No encodable faces found in training images.", 500

    marked_today = set()

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
                    student_id_str = validNames[matchIndex]

                    if student_id_str not in marked_today:
                        success_db = markData(student_id_str)
                        success_csv = markAttendanceCSV(student_id_str)

                        if success_db or success_csv:
                            marked_today.add(student_id_str)

                    conn = connect_db()
                    try:
                        st = get_student_for_course(conn, int(student_id_str), course_id)
                    finally:
                        conn.close()

                    display_name = str(st["Name"]) if st else "Unknown"
                    if student_id_str in marked_today:
                        display_name = f"{display_name} ✓"
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
    input_user = request.form.get("username", "").strip().lower()
    input_pass = request.form.get("password", "").strip()

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
        if request.form:
            flash("Missing username or password", "error")
            return redirect(url_for("how"))
        return "failed"

    if not CRED_CSV.exists():
        if request.form:
            flash("Credential file missing", "error")
            return redirect(url_for("how"))
        return "failed"

    try:
        with open(CRED_CSV, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
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
        course_id_raw = (request.form.get("course_id") or "").strip()
        dept_id_raw = (request.form.get("department_id") or "").strip()
        major_id_raw = (request.form.get("major_id") or "").strip()
        conn = connect_db()
        try:
            q = """SELECT s.Name AS NAME, s.RollNo AS RollNo, a.Time AS Time, a.Date AS Date,
                          d.Name AS DepartmentName, m.Name AS MajorName, c.Name AS CourseName
                   FROM AttendanceV2 a
                   JOIN StudentsV2 s ON s.Id=a.StudentId
                   JOIN Courses c ON c.Id=a.CourseId
                   JOIN Departments d ON d.Id=c.DepartmentId
                   LEFT JOIN Majors m ON m.Id=c.MajorId
                   WHERE a.Date=?"""
            params = [today]  # type: List[object]
            if dept_id_raw.isdigit():
                q += " AND d.Id=?"
                params.append(int(dept_id_raw))
            if major_id_raw.isdigit():
                q += " AND m.Id=?"
                params.append(int(major_id_raw))
            if course_id_raw.isdigit():
                q += " AND c.Id=?"
                params.append(int(course_id_raw))
            q += " ORDER BY a.Time ASC"
            rows = conn.execute(q, tuple(params)).fetchall()
            departments = list_departments(conn)
            majors = list_majors(conn)
            courses = list_courses(conn)
        finally:
            conn.close()
        return render_template("form2.html", rows=rows, departments=departments, majors=majors, courses=courses)

    return render_template("form1.html")


@app.route("/attendance/today/report.pdf", methods=["GET"])
@login_required
def download_todays_attendance_pdf():
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError:
        flash("PDF export requires the 'reportlab' package. Install it with: pip install reportlab", "error")
        return redirect(url_for("data"))

    today = str(date.today())
    dept_id_raw = (request.args.get("department_id") or "").strip()
    major_id_raw = (request.args.get("major_id") or "").strip()
    course_id_raw = (request.args.get("course_id") or "").strip()
    conn = connect_db()
    try:
        q = """SELECT s.Name AS NAME, s.RollNo AS RollNo, a.Time AS Time, a.Date AS Date,
                      d.Name AS DepartmentName, m.Name AS MajorName, c.Name AS CourseName
               FROM AttendanceV2 a
               JOIN StudentsV2 s ON s.Id=a.StudentId
               JOIN Courses c ON c.Id=a.CourseId
               JOIN Departments d ON d.Id=c.DepartmentId
               LEFT JOIN Majors m ON m.Id=c.MajorId
               WHERE a.Date=?"""
        params = [today]  # type: List[object]
        if dept_id_raw.isdigit():
            q += " AND d.Id=?"
            params.append(int(dept_id_raw))
        if major_id_raw.isdigit():
            q += " AND m.Id=?"
            params.append(int(major_id_raw))
        if course_id_raw.isdigit():
            q += " AND c.Id=?"
            params.append(int(course_id_raw))
        q += " ORDER BY a.Time ASC"
        rows = conn.execute(q, tuple(params)).fetchall()
    finally:
        conn.close()

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    margin_x = 40
    y = height - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin_x, y, "Today's Attendance Report")

    y -= 22
    c.setFont("Helvetica", 11)
    c.drawString(margin_x, y, f"Date: {today}")

    y -= 22
    c.setFont("Helvetica-Bold", 10)
    c.drawString(margin_x, y, "Name")
    c.drawString(margin_x + 220, y, "Department")
    c.drawString(margin_x + 310, y, "Major")
    c.drawString(margin_x + 410, y, "Course")
    c.drawString(margin_x + 505, y, "Time")

    y -= 8
    c.setLineWidth(0.7)
    c.line(margin_x, y, width - margin_x, y)
    y -= 16

    c.setFont("Helvetica", 10)
    if not rows:
        c.drawString(margin_x, y, "No attendance records found for today.")
    else:
        for r in rows:
            name = str(r["NAME"] or "")
            dept = str(r["DepartmentName"] or "")
            major = str(r["MajorName"] or "")
            course = str(r["CourseName"] or "")
            tm = str(r["Time"] or "")

            if y < 60:
                c.showPage()
                y = height - 50
                c.setFont("Helvetica-Bold", 16)
                c.drawString(margin_x, y, "Today's Attendance Report")
                y -= 22
                c.setFont("Helvetica", 11)
                c.drawString(margin_x, y, f"Date: {today}")
                y -= 22
                c.setFont("Helvetica-Bold", 10)
                c.drawString(margin_x, y, "Name")
                c.drawString(margin_x + 220, y, "Department")
                c.drawString(margin_x + 310, y, "Major")
                c.drawString(margin_x + 410, y, "Course")
                c.drawString(margin_x + 505, y, "Time")
                y -= 8
                c.setLineWidth(0.7)
                c.line(margin_x, y, width - margin_x, y)
                y -= 16
                c.setFont("Helvetica", 10)

            c.drawString(margin_x, y, name[:22])
            c.drawString(margin_x + 220, y, dept[:12])
            c.drawString(margin_x + 310, y, major[:12])
            c.drawString(margin_x + 410, y, course[:12])
            c.drawString(margin_x + 505, y, tm[:10])
            y -= 14

    c.showPage()
    c.save()
    buffer.seek(0)

    filename = f"attendance_{today}.pdf"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf",
    )


@app.route("/whole", methods=["GET", "POST"])
@login_required 
def whole():
    conn = connect_db()
    try:
        rows = conn.execute(
            """SELECT s.Name AS NAME, s.RollNo AS RollNo, a.Time AS Time, a.Date AS Date,
                      d.Name AS DepartmentName, m.Name AS MajorName, c.Name AS CourseName
               FROM AttendanceV2 a
               JOIN StudentsV2 s ON s.Id=a.StudentId
               JOIN Courses c ON c.Id=a.CourseId
               JOIN Departments d ON d.Id=c.DepartmentId
               LEFT JOIN Majors m ON m.Id=c.MajorId
               ORDER BY a.Date DESC, a.Time DESC"""
        ).fetchall()
    finally:
        conn.close()
    return render_template("form3.html", rows=rows)


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    today = str(date.today())
    conn = connect_db()
    try:
        rows = conn.execute(
            """SELECT s.Name AS NAME, s.RollNo AS RollNo, a.Time AS Time, a.Date AS Date,
                      d.Name AS DepartmentName, m.Name AS MajorName, c.Name AS CourseName
               FROM AttendanceV2 a
               JOIN StudentsV2 s ON s.Id=a.StudentId
               JOIN Courses c ON c.Id=a.CourseId
               JOIN Departments d ON d.Id=c.DepartmentId
               LEFT JOIN Majors m ON m.Id=c.MajorId
               WHERE a.Date=?
               ORDER BY a.Time ASC""",
            (today,),
        ).fetchall()
        departments = list_departments(conn)
        majors = list_majors(conn)
        courses = list_courses(conn)
    finally:
        conn.close()
    return render_template("form2.html", rows=rows, departments=departments, majors=majors, courses=courses)


@app.route("/catalog", methods=["GET"])
@login_required
def catalog():
    conn = connect_db()
    try:
        departments = list_departments(conn)
        majors = list_majors(conn)
        courses = list_courses(conn)
    finally:
        conn.close()
    return render_template("catalog.html", departments=departments, majors=majors, courses=courses, 
                          flashed_messages=get_flashed_messages(with_categories=True))


@app.route("/catalog/departments", methods=["POST"])
@login_required
def create_department():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Department name is required.", "error")
        return redirect(url_for("catalog"))

    conn = connect_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO Departments (Name, Slug) VALUES (?, ?)",
            (name, slugify(name)),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/catalog/courses", methods=["POST"])
@login_required
def create_course():
    name = (request.form.get("name") or "").strip()
    major_id_raw = (request.form.get("major_id") or "").strip()
    if not name or not major_id_raw.isdigit():
        flash("Course name and Major are required.", "error")
        return redirect(url_for("catalog"))

    major_id = int(major_id_raw)
    conn = connect_db()
    try:
        mrow = conn.execute(
            "SELECT Id, DepartmentId FROM Majors WHERE Id=?",
            (major_id,),
        ).fetchone()
        if not mrow:
            flash("Invalid major.", "error")
            return redirect(url_for("catalog"))
        department_id = int(mrow["DepartmentId"])
        conn.execute(
            "INSERT OR IGNORE INTO Courses (DepartmentId, MajorId, Name, Slug) VALUES (?, ?, ?, ?)",
            (department_id, major_id, name, slugify(name)),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/catalog/majors", methods=["POST"])
@login_required
def create_major():
    name = (request.form.get("name") or "").strip()
    dept_id_raw = (request.form.get("department_id") or "").strip()
    if not name or not dept_id_raw.isdigit():
        flash("Major name and School are required.", "error")
        return redirect(url_for("catalog"))

    department_id = int(dept_id_raw)
    conn = connect_db()
    try:
        dept = conn.execute("SELECT Id FROM Departments WHERE Id=?", (department_id,)).fetchone()
        if not dept:
            flash("Invalid school.", "error")
            return redirect(url_for("catalog"))
        conn.execute(
            "INSERT OR IGNORE INTO Majors (DepartmentId, Name, Slug) VALUES (?, ?, ?)",
            (department_id, name, slugify(name)),
        )
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/catalog/departments/<int:department_id>/delete", methods=["POST"])
@login_required
def delete_department(department_id):
    conn = connect_db()
    try:
        dept = conn.execute("SELECT Id FROM Departments WHERE Id=?", (department_id,)).fetchone()
        if not dept:
            flash("School not found.", "error")
            return redirect(url_for("catalog"))
        
        majors_count = conn.execute("SELECT COUNT(*) FROM Majors WHERE DepartmentId=?", (department_id,)).fetchone()[0]
        if majors_count > 0:
            flash(f"Cannot delete school: {majors_count} major(s) are associated with this school.", "error")
            return redirect(url_for("catalog"))
        
        students_count = conn.execute("""
            SELECT COUNT(*) FROM Enrollments e 
            JOIN Courses c ON c.Id = e.CourseId 
            JOIN Majors m ON m.Id = c.MajorId 
            WHERE m.DepartmentId = ?
        """, (department_id,)).fetchone()[0]
        if students_count > 0:
            flash(f"Cannot delete school: {students_count} student(s) are enrolled in courses under this school.", "error")
            return redirect(url_for("catalog"))
        
        conn.execute("DELETE FROM Departments WHERE Id=?", (department_id,))
        conn.commit()
        flash("School deleted successfully.", "success")
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/catalog/majors/<int:major_id>/delete", methods=["POST"])
@login_required
def delete_major(major_id):
    conn = connect_db()
    try:
        major = conn.execute("SELECT Id FROM Majors WHERE Id=?", (major_id,)).fetchone()
        if not major:
            flash("Major not found.", "error")
            return redirect(url_for("catalog"))
        
        courses_count = conn.execute("SELECT COUNT(*) FROM Courses WHERE MajorId=?", (major_id,)).fetchone()[0]
        if courses_count > 0:
            flash(f"Cannot delete major: {courses_count} course(s) are associated with this major.", "error")
            return redirect(url_for("catalog"))
        
        students_count = conn.execute("""
            SELECT COUNT(*) FROM Enrollments e 
            JOIN Courses c ON c.Id = e.CourseId 
            WHERE c.MajorId = ?
        """, (major_id,)).fetchone()[0]
        if students_count > 0:
            flash(f"Cannot delete major: {students_count} student(s) are enrolled in courses under this major.", "error")
            return redirect(url_for("catalog"))
        
        conn.execute("DELETE FROM Majors WHERE Id=?", (major_id,))
        conn.commit()
        flash("Major deleted successfully.", "success")
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/catalog/courses/<int:course_id>/delete", methods=["POST"])
@login_required
def delete_course(course_id):
    conn = connect_db()
    try:
        course = conn.execute("SELECT Id FROM Courses WHERE Id=?", (course_id,)).fetchone()
        if not course:
            flash("Course not found.", "error")
            return redirect(url_for("catalog"))
        
        students_count = conn.execute("SELECT COUNT(*) FROM Enrollments WHERE CourseId=?", (course_id,)).fetchone()[0]
        if students_count > 0:
            flash(f"Cannot delete course: {students_count} student(s) are enrolled in this course.", "error")
            return redirect(url_for("catalog"))
        
        attendance_count = conn.execute("SELECT COUNT(*) FROM AttendanceV2 WHERE CourseId=?", (course_id,)).fetchone()[0]
        if attendance_count > 0:
            flash(f"Cannot delete course: {attendance_count} attendance record(s) are associated with this course.", "error")
            return redirect(url_for("catalog"))
        
        conn.execute("DELETE FROM Courses WHERE Id=?", (course_id,))
        conn.commit()
        flash("Course deleted successfully.", "success")
    finally:
        conn.close()
    return redirect(url_for("catalog"))


@app.route("/students", methods=["GET"])
@login_required
def manage_students():
    conn = connect_db()
    try:
        students = conn.execute("""
            SELECT s.Id, s.Name, s.RollNo,
                   d.Name AS DepartmentName,
                   m.Name AS MajorName,
                   c.Name AS CourseName,
                   e.CourseId,
                   e.StudentId
            FROM StudentsV2 s
            JOIN Enrollments e ON e.StudentId = s.Id
            JOIN Courses c ON c.Id = e.CourseId
            JOIN Departments d ON d.Id = s.DepartmentId
            LEFT JOIN Majors m ON m.Id = c.MajorId
            ORDER BY d.Name, m.Name, c.Name, s.Name
        """).fetchall()
        
        departments = list_departments(conn)
        majors = list_majors(conn)
        courses = list_courses(conn)
    finally:
        conn.close()
    
    return render_template("students.html", students=students, departments=departments, majors=majors, courses=courses,
                          flashed_messages=get_flashed_messages(with_categories=True))


@app.route("/students/enrollment/<int:student_id>/<int:course_id>/delete", methods=["POST"])
@login_required
def delete_enrollment(student_id, course_id):
    conn = connect_db()
    try:
        conn.execute("DELETE FROM Enrollments WHERE StudentId=? AND CourseId=?", (student_id, course_id))
        conn.commit()
        flash("Student unenrolled from course successfully.", "success")
    finally:
        conn.close()
    return redirect(url_for("manage_students"))


@app.route("/attendance", methods=["GET"])
@login_required
def manage_attendance():
    conn = connect_db()
    try:
        attendance = conn.execute("""
            SELECT a.Id, a.Time, a.Date,
                   s.Name AS StudentName, s.RollNo,
                   d.Name AS DepartmentName,
                   m.Name AS MajorName,
                   c.Name AS CourseName,
                   a.CourseId
            FROM AttendanceV2 a
            JOIN StudentsV2 s ON s.Id = a.StudentId
            JOIN Courses c ON c.Id = a.CourseId
            JOIN Departments d ON d.Id = s.DepartmentId
            LEFT JOIN Majors m ON m.Id = c.MajorId
            ORDER BY a.Date DESC, a.Time DESC
        """).fetchall()
        
        departments = list_departments(conn)
        majors = list_majors(conn)
        courses = list_courses(conn)
    finally:
        conn.close()
    
    return render_template("attendance.html", attendance=attendance, departments=departments, majors=majors, courses=courses,
                          flashed_messages=get_flashed_messages(with_categories=True))


@app.route("/attendance/<int:attendance_id>/delete", methods=["POST"])
@login_required
def delete_attendance(attendance_id):
    conn = connect_db()
    try:
        conn.execute("DELETE FROM AttendanceV2 WHERE Id=?", (attendance_id,))
        conn.commit()
        flash("Attendance record deleted successfully.", "success")
    finally:
        conn.close()
    return redirect(url_for("manage_attendance"))


@app.route("/generate-chart", methods=["GET"])
@login_required
def generate_chart():
    """Generate attendance statistics bar chart (by days, deduplicated)"""
    conn = connect_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT Name FROM Students")
        all_students = [row['Name'] for row in cur.fetchall()]

        student_attendance = {name: 0 for name in all_students}

        cur.execute("""
                    SELECT NAME, COUNT(DISTINCT Date) as days
                    FROM Attendance
                    GROUP BY NAME
                    """)

        for record in cur.fetchall():
            name = record['NAME']
            if name in student_attendance:
                student_attendance[name] = record['days']

    finally:
        conn.close()

    if not student_attendance or sum(student_attendance.values()) == 0:
        flash("No attendance data available", "error")
        return redirect(url_for("dashboard"))

    fig, ax = plt.subplots(figsize=(14, 7))

    names = list(student_attendance.keys())
    counts = list(student_attendance.values())

    bars = ax.bar(names, counts, color='#667eea', edgecolor='#764ba2', linewidth=2)

    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2., height,
                f'{int(height)}',
                ha='center', va='bottom', fontsize=12, fontweight='bold')

    ax.set_xlabel('Student Name', fontsize=14, fontweight='bold')
    ax.set_ylabel('Attendance Days', fontsize=14, fontweight='bold')
    ax.set_title('Student Attendance Statistics (by Days)', fontsize=18, fontweight='bold', pad=20)
    ax.grid(axis='y', alpha=0.3, linestyle='--')

    plt.xticks(rotation=45, ha='right', fontsize=11)
    plt.tight_layout()

    img_io = BytesIO()
    plt.savefig(img_io, format='png', dpi=300, bbox_inches='tight')
    img_io.seek(0)
    plt.close()

    today = str(date.today())
    return send_file(
        img_io,
        mimetype='image/png',
        as_attachment=True,
        download_name=f'attendance_chart_{today}.png'
    )


@app.route("/generate-chart-rate", methods=["GET"])
@login_required
def generate_chart_rate():
    """Generate attendance rate bar chart with color differentiation"""
    conn = connect_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT Name FROM Students")
        all_students = [row['Name'] for row in cur.fetchall()]

        cur.execute("SELECT COUNT(DISTINCT Date) as days FROM Attendance")
        total_days_result = cur.fetchone()
        total_days = total_days_result['days'] if total_days_result else 1

        if total_days == 0:
            total_days = 1

        student_attendance = {name: 0 for name in all_students}

        cur.execute("""
                    SELECT NAME, COUNT(DISTINCT Date) as days
                    FROM Attendance
                    GROUP BY NAME
                    """)

        for record in cur.fetchall():
            name = record['NAME']
            if name in student_attendance:
                student_attendance[name] = record['days']

    finally:
        conn.close()

    if not student_attendance:
        flash("No student data available", "error")
        return redirect(url_for("dashboard"))

    names = list(student_attendance.keys())
    rates = [(count / total_days * 100) for count in student_attendance.values()]

    colors = []
    for rate in rates:
        if rate >= 80:
            colors.append('#2ecc71')
        elif rate >= 60:
            colors.append('#f39c12')
        else:
            colors.append('#e74c3c')

    fig, ax = plt.subplots(figsize=(14, 7))

    bars = ax.bar(names, rates, color=colors, edgecolor='black', linewidth=1.5)

    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2., height,
                f'{height:.1f}%',
                ha='center', va='bottom', fontsize=11, fontweight='bold')

    ax.set_xlabel('Student Name', fontsize=14, fontweight='bold')
    ax.set_ylabel('Attendance Rate (%)', fontsize=14, fontweight='bold')
    ax.set_title(f'Student Attendance Rate Statistics (Total Days: {total_days})',
                 fontsize=18, fontweight='bold', pad=20)
    ax.set_ylim(0, 105)
    ax.grid(axis='y', alpha=0.3, linestyle='--')

    ax.axhline(y=80, color='green', linestyle='--', alpha=0.5, linewidth=2)
    ax.axhline(y=60, color='orange', linestyle='--', alpha=0.5, linewidth=2)

    legend_elements = [
        Patch(facecolor='#2ecc71', label='Excellent (≥80%)'),
        Patch(facecolor='#f39c12', label='Good (60-80%)'),
        Patch(facecolor='#e74c3c', label='Need Improvement (<60%)')
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=11)

    plt.xticks(rotation=45, ha='right', fontsize=11)
    plt.tight_layout()

    img_io = BytesIO()
    try:
        plt.savefig(img_io, format='png', dpi=300, bbox_inches='tight')
        img_io.seek(0)
    except Exception as e:
        print(f"Error saving chart: {e}")
        flash(f"Error generating chart: {str(e)}", "error")
        return redirect(url_for("dashboard"))
    finally:
        plt.close()

    today = str(date.today())
    return send_file(
        img_io,
        mimetype='image/png',
        as_attachment=True,
        download_name=f'attendance_rate_{today}.png'
    )


if __name__ == "__main__":
    app.run(debug=True)