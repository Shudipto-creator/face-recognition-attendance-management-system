# Face Recognition–Based Attendance Management System

## Abstract

This project presents a web-based attendance management system that utilizes face recognition technology to automatically identify students and record attendance in real time. The system aims to reduce manual effort, minimize human error, and demonstrate the practical application of computer vision and machine learning techniques in an academic environment.

The implementation is based on Python and Flask, integrates classical face embedding–based recognition methods, and stores attendance data using a lightweight relational database. The project is intended strictly for academic experimentation, learning, and small-scale demonstrations.

---

## Objectives

The primary objectives of this project are:

1. To design and implement an automated attendance system using face recognition.
2. To apply computer vision techniques for real-time face detection and identification.
3. To develop a web-based administrative interface for managing student records.
4. To analyze the feasibility of deploying face recognition systems in educational contexts.

---

## System Overview

The system captures live video from a webcam, detects faces in real time, extracts facial embeddings, and compares them with stored embeddings generated from registered student images. When a match is identified, attendance is recorded automatically in the database.

---

## Key Features

- Automated face recognition–based attendance
- Real-time webcam-based face detection
- Student record management (add, update, delete)
- Training image dataset management
- Attendance report generation and CSV export
- Admin authentication system
- Web-based dashboard interface

---

## Technology Stack

### Backend
- Python
- Flask

### Face Recognition & Image Processing
- face-recognition
- dlib
- OpenCV
- NumPy
- Pillow (PIL)

### Database
- SQLite

### Frontend
- HTML
- CSS
- JavaScript
- Bootstrap

---

## System Architecture

### High-Level Architecture

```mermaid
graph TD
    A[Webcam] --> B[Face Detection & Encoding]
    B --> C[Face Recognition Engine]
    C --> D[Attendance Logic]
    D --> E[SQLite Database]
    E --> F[Admin Dashboard]
    F --> G[Web Browser]
