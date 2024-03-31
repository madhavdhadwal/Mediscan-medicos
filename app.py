from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
import os
import boto3
from pprint import pprint
import csv
import re
import tempfile

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

app.secret_key = 'xxxmozohackxxx'  
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///mediscan.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.app_context().push()

class User(db.Model):
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True)
    username = db.Column(db.String(length=50), nullable=False)
    password = db.Column(db.String(length=100), nullable=False)  # New column for password
    submissions = db.relationship('Submission', backref='user', lazy=True)  # Relationship with Submission model
class Submission(db.Model):
    id = db.Column(db.Integer(), primary_key=True, autoincrement=True)
    username = db.Column(db.Integer(), db.ForeignKey('user.id'), nullable=False)  # Foreign key relationship with User model
    file_name = db.Column(db.String(length=255), nullable=False)  # File name of the submission
    # Add more columns for submission details as needed

def get_rows_columns_map(table_result, blocks_map):
    rows = {}
    scores = []
    for relationship in table_result["Relationships"]:
        if relationship["Type"] == "CHILD":
            for child_id in relationship["Ids"]:
                cell = blocks_map[child_id]
                if cell["BlockType"] == "CELL":
                    row_index = cell["RowIndex"]
                    col_index = cell["ColumnIndex"]
                    if row_index not in rows:
                        rows[row_index] = {}
                    scores.append(str(cell["Confidence"]))
                    rows[row_index][col_index] = get_text(cell, blocks_map)
    return rows, scores

def get_text(result, blocks_map):
    text = ""
    if "Relationships" in result:
        for relationship in result["Relationships"]:
            if relationship["Type"] == "CHILD":
                for child_id in relationship["Ids"]:
                    word = blocks_map[child_id]
                    if word["BlockType"] == "WORD":
                        text += word["Text"] + " "
                    if word["BlockType"] == "SELECTION_ELEMENT":
                        if word["SelectionStatus"] == "SELECTED":
                            text += "X "
    return text.strip()

def is_float(value):
    try:
        float(value)
        return True
    except ValueError:
        return False

def parse_bio_ref_interval(interval):
    if "-" in interval:
        parts = interval.replace(" ", "").split("-")
    else:
        parts = interval.split()

    lower = None
    upper = None

    if len(parts) == 2:
        lower = float(parts[0])
        upper = float(parts[1])
    elif "<" in interval:
        upper = float(parts[0][1:])
    elif ">" in interval:
        lower = float(parts[0][1:])
    elif len(parts) == 1 and is_float(parts[0]):
        upper = float(parts[0])

    return lower, upper

def process_table_data(rows, output_file):
    with open(output_file, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["Test Name", "Results", "Units", "Bio. Ref. Interval", "Above Reference?"]
        )

        for row_index, cols in rows.items():
            test_name = cols.get(1, "").strip()
            result_str = cols.get(2, "").strip()
            units = cols.get(3, "").strip()
            bio_ref_interval = cols.get(4, "").strip()
            bio_ref_interval = re.sub(" +", " ", bio_ref_interval)

            if is_float(result_str) and test_name and bio_ref_interval:
                result = float(result_str)
                lower, upper = parse_bio_ref_interval(bio_ref_interval)

                above_reference = "No"
                if (upper is not None and result > upper) or (
                    lower is not None and result < lower
                ):
                    above_reference = "Yes"

                writer.writerow(
                    [
                        test_name,
                        result,
                        units,
                        "-".join(bio_ref_interval.split(" ")),
                        above_reference,
                    ]
                )

def analyze_document(file_data, output_csv):
    client = boto3.client(
        "textract",
        region_name="ap-south-1",
        aws_access_key_id="",              #enter your aws access key
        aws_secret_access_key="",          #enter your secret access key       
    )

    response = client.analyze_document(
        Document={"Bytes": file_data}, FeatureTypes=["TABLES"]
    )

    blocks = response["Blocks"]
    blocks_map = {block["Id"]: block for block in blocks if "Id" in block}

    table_blocks = [block for block in blocks if block["BlockType"] == "TABLE"]
    if len(table_blocks) < 3:
        print("Less than 3 tables found in document.")
        return

    third_table = table_blocks[2]
    rows, scores = get_rows_columns_map(third_table, blocks_map)
    process_table_data(rows, output_csv)

@app.route('/')


@app.route('/home')
def home():
    print(app.template_folder)
    return render_template('home.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()
        if user:
            # User is authenticated, set session variable
            session['user_id'] = user.id
            return redirect(url_for('home'))
        else:
            # Authentication failed, render login page with error message
            return render_template('login.html', error='Invalid username or password.')
    return render_template('login.html')

@app.route('/logout')
def logout():
    # Clear session variable to log user out
    session.pop('user_id', None)
    return redirect(url_for('home'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        # Check if username is already taken
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return render_template('register.html', error='Username already taken.')
        # Create a new user and add to the database
        new_user = User( username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        # Redirect to login page after successful registration
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/submissions')
def submissions():
    # Check if user is logged in
    if 'user_id' not in session:
        flash('You need to log in to access submissions.')
        return redirect(url_for('login'))  
    user_id = session['user_id']
    submissions = Submission.query.filter_by(username=user_id).all()
    file_paths = []
    for submission in submissions:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], submission.file_name)
        if os.path.exists(file_path):
            file_paths.append(file_path)
    return render_template('submissions.html', user_id=user_id, file_paths=file_paths)

@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        flash('No file part')
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.url)
    if file:
        # Save the file to a temporary location
        temp_file = tempfile.NamedTemporaryFile(delete=False)
        file.save(temp_file.name)

        # Perform analysis on the uploaded file
        output_csv = tempfile.NamedTemporaryFile(delete=False).name
        analyze_document(temp_file.read(), output_csv)
        
        # Read the output CSV file
        with open(output_csv, 'r') as f:
            output_data = f.read()

        # Close and delete temporary files
        temp_file.close()
        os.unlink(temp_file.name)
        os.unlink(output_csv)

        # Render a template to display the output data
        return render_template('output.html', output_data=output_data)


@app.route('/user')
def user():
    return render_template('user.html')





if __name__ == "__main__":
    app.run(debug=True)