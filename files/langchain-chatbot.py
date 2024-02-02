#----------------------------------
# IMPORTS
#----------------------------------
## Import Pyarrow
from pyarrow import flight
from pyarrow.flight import FlightClient
import pyarrow.dataset as ds
from pyspark.sql import SparkSession
from pyspark.sql.types import *
import requests
from flask import Flask,request

from langchain.agents import create_spark_sql_agent
from langchain_community.agent_toolkits import SparkSQLToolkit
from langchain_community.utilities.spark_sql import SparkSQL
from langchain_openai import ChatOpenAI
from langchain_core import exceptions
import re
import os

#----------------------------------
# Setup
#----------------------------------
app = Flask(__name__)
# Initialize Spark session
spark = SparkSession.builder \
    .appName("DremioToSparkSQLExample") \
    .getOrCreate()
schema = "langchain_example"
spark.sql(f"CREATE DATABASE IF NOT EXISTS {schema}")
spark.sql(f"USE {schema}")

token = os.environ['DREMIO_PROD_KEY']

## Headers for Authentication
headers = [
    (b"authorization", f"bearer {token}".encode("utf-8"))
    ]

## Create Client
client = FlightClient(location=("grpc+tls://data.dremio.cloud:443"))

def convert_to_quoted_path(path):
    # Split the path by slashes and quote each part
    parts = path.split('/')
    quoted_parts = ['"{}"'.format(part) for part in parts]
    # Join the quoted parts with periods
    quoted_path = '.'.join(quoted_parts)
    return quoted_path

def make_query(query, client, headers):
    ## Get Schema Description and build headers
    flight_desc = flight.FlightDescriptor.for_command(query)
    options = flight.FlightCallOptions(headers=headers)
    schema = client.get_schema(flight_desc, options)

    ## Get ticket to for query execution, used to get results
    flight_info = client.get_flight_info(flight.FlightDescriptor.for_command(query), options)
    
    ## Get Results 
    results = client.do_get(flight_info.endpoints[0].ticket, options)
    return results

@app.route('/genai-response', methods=['POST'])
def genAiResponse():
    # Get the JSON from the POST request body
    try:
        json_array = request.get_json()
        msg = json_array.get('msg')
        # The API endpoint you want to call
        url = 'http://datamgmtdemo01.eastasia.cloudapp.azure.com/listalldatasets?env=PROD'
        # Perform the GET request
        response = requests.get(url)
        
        # Check if the request was successful
        if response.status_code == 200:
            # Parse the response JSON content
            data = response.json()
            result_array = []
        
            for path in data:
                lastField = path.split("/")[-1]
                path = convert_to_quoted_path(path)
                #----------------------------------
                # Run Query
                #----------------------------------
        
                ## Query Dremio, get back Arrow FlightStreamReader
                print(f"Making query for {path}")
                results = make_query(
                f"""
                SELECT * FROM {path}; 
                """
                , client, headers)
        
                print(f"Fetching result for {path}")
                ## Convert StreamReader into an Arrow Table
                table = results.read_all()
                sdf = spark.createDataFrame(table.to_pandas())
                # Create a temporary view to run Spark SQL queries
                sdf.write.saveAsTable(lastField)
        
        else:
            print(f"Failed to fetch data: {response.status_code} {response.reason}")
        
        spark_sql = SparkSQL(schema=schema)
        llm = ChatOpenAI(model="gpt-4-turbo-preview",temperature=0)
        toolkit = SparkSQLToolkit(db=spark_sql, llm=llm)
        agent_executor = create_spark_sql_agent(llm=llm, toolkit=toolkit, verbose=True,handle_parsing_errors=True)       
        result = agent_executor.run(msg)
        spark.sql(f"DROP DATABASE IF NOT EXISTS {schema}")
        return result
    except exceptions.OutputParserException as e:
        # Handle the specific OutputParserException
        error_message = str(e)
        print(f"OutputParserException caught: {error_message}", flush=True)
        # Extract meaningful error message if it matches the expected pattern
        if error_message.startswith("Could not parse LLM output: `"):
            error_message = error_message.removeprefix("Could not parse LLM output: `").removesuffix("`")
        #return jsonify({"error": "Output parsing error", "details": error_message}), 500
    except ValueError as e:
        # Handle any other ValueError that might be related to parsing
        error_message = str(e)
        print(f"ValueError caught: {error_message}", flush=True)
        match = re.search(r"Could not parse LLM output: `([^`]*)`", error_message)

        # Check if we found a match
        if match:
            extracted_message = match.group(1)  # This is "I don't know"
            return(extracted_message)
        else:
            return("I don't know")
        #return jsonify({"error": "ValueError", "details": error_message}), 500
    except Exception as e:
        # General exception handler for any unexpected exceptions
        error_message = str(e)
        print(f"Unexpected error caught: {error_message}", flush=True)
        #return jsonify({"error": "Unexpected error", "details": error_message}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5201)