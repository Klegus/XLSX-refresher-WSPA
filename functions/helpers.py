import datetime
import hashlib
from functions.log import log
from pymongo import MongoClient, DESCENDING
def format_time_range(time_range):
    start_time_str, end_time_str = time_range.split('-')
    start_time = datetime.datetime.strptime(start_time_str.strip(), '%H%M').time()
    end_time = datetime.datetime.strptime(end_time_str.strip(), '%H%M').time()
    formatted_time_range = f"{start_time.strftime('%H<sup>%M</sup>')}-{end_time.strftime('%H<sup>%M</sup>')}"
    return formatted_time_range
def get_datetime_range(time_range):
    start_time_str, end_time_str = time_range.split('-')
    start_time = datetime.datetime.strptime(start_time_str.strip(), '%H%M').time()
    end_time = datetime.datetime.strptime(end_time_str.strip(), '%H%M').time()
    return [start_time, end_time]

def calculate_checksum(file_path):
    with open(file_path, 'rb') as f:
        bytes = f.read() # read entire file as bytes
        readable_hash = hashlib.sha256(bytes).hexdigest()
        return readable_hash

def db_insert_mongodb(values, checksum, time_speed):
    """
    Insert a document into a MongoDB collection
    """
    
    # Create a MongoDB client, change "your_connection_string" to your actual connection string
    client = MongoClient("mongodb://mongo:ca15bbfhDH4Gf-5D6bE1DdFC54-CFACG@viaduct.proxy.rlwy.net:32898")
    
    # Specify the database and collection
    db = client["LessonPlan"]
    collection = db["plans"]
    document = {}
    for i, val in enumerate(values):
        document[f"group{i+1}"] = val
    document["time_update"] = datetime.datetime.now()
    document["time_speed"] = time_speed
    document["checksum"] = checksum

    # Insert the document into the collection
    collection.insert_one(document)

    # Close the client
    client.close()
def get_latest_checksum():
    """
    Get the checksum from the latest document in a MongoDB collection
    """
    
    # Create a MongoDB client, change "your_connection_string" to your actual connection string
    client = MongoClient("mongodb://mongo:ca15bbfhDH4Gf-5D6bE1DdFC54-CFACG@viaduct.proxy.rlwy.net:32898")
    
    # Specify the database and collection
    db = client["LessonPlan"]
    collection = db["plans"]

    # Get the latest document
    latest_document = collection.find_one(sort=[('_id', DESCENDING)])

    # Close the client
    client.close()

    # Return the checksum
    return latest_document['checksum'] if latest_document else None
