FROM apache/airflow:2.10.3-python3.11
USER root
RUN pip install --no-cache-dir \
    duckdb \
    boto3 \
    pyarrow \
    google-genai \
    scikit-learn
USER airflow
