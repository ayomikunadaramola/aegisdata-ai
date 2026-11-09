
"""AWS Athena client for the AegisData AI dashboard.

Run from the project root:
    python dashboard/athena_client.py

Exports:
    run_query(sql, timeout_seconds=120) -> pandas.DataFrame
"""

import os
import re
import time

import boto3
import pandas as pd
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


# ============================================================
# AWS CONFIGURATION
# ============================================================

AWS_PROFILE = os.getenv("AWS_PROFILE", "aegisdata")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ATHENA_DATABASE = "aegisdata_gold_db"
ATHENA_WORKGROUP = "primary"

ATHENA_OUTPUT = (
    "s3://aegisdata-ai-941490574194-us-east-1/"
    "athena-results/"
)


# ============================================================
# ATHENA CLIENT
# ============================================================

def get_athena_client():
    """Create an authenticated AWS Athena client."""

    session = boto3.Session(
        profile_name=AWS_PROFILE,
        region_name=AWS_REGION,
    )

    return session.client(
        "athena",
        config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={
                "max_attempts": 3,
                "mode": "standard",
            },
        ),
    )


# ============================================================
# SQL NORMALIZATION
# ============================================================


def normalize_sql(sql: str) -> str:
    """Normalize SQL and allow SELECT/WITH statements."""

    if not isinstance(sql, str):
        raise TypeError("SQL must be a string.")

    statement = sql.strip()

    # Remove leading line comments and block comments.
    while statement:
        if statement.startswith("--"):
            newline = statement.find("\n")

            if newline == -1:
                statement = ""
            else:
                statement = statement[newline + 1:].lstrip()

        elif statement.startswith("/*"):
            end = statement.find("*/", 2)

            if end == -1:
                raise ValueError("Unclosed SQL block comment.")

            statement = statement[end + 2:].lstrip()

        else:
            break

    statement = statement.strip().rstrip(";").strip()

    if not statement:
        raise ValueError("SQL query cannot be empty.")

    if not re.match(
        r"^(SELECT|WITH)\b",
        statement,
        flags=re.IGNORECASE,
    ):
        raise ValueError(
            "Only SELECT/WITH queries are supported. "
            f"Query starts with: {statement[:80]!r}"
        )

    return statement

    # ============================================================
    # ATHENA QUERY EXECUTION
    # ============================================================


def run_query(sql, timeout_seconds=120):
    """Execute an Athena query and return all rows as a DataFrame.

    Handles:
        - Leading SQL comments
        - AWS authentication
        - Query execution
        - Execution status polling
        - Query failures
        - Timeout detection
        - Complete result pagination
        - DataFrame construction

        All returned column values are initially strings or None.
        """

    statement = normalize_sql(sql)

    if timeout_seconds <= 0:
        raise ValueError(
            "timeout_seconds must be greater than zero."
        )

    client = get_athena_client()

    # --------------------------------------------------------
    # START QUERY
    # --------------------------------------------------------

    try:
        response = client.start_query_execution(
            QueryString=statement,
            QueryExecutionContext={
                "Database": ATHENA_DATABASE,
            },
            ResultConfiguration={
                "OutputLocation": ATHENA_OUTPUT,
            },
            WorkGroup=ATHENA_WORKGROUP,
            ResultReuseConfiguration={
                "ResultReuseByAgeConfiguration": {
                    "Enabled": True,
                    "MaxAgeInMinutes": 60,
                }
            },
        )

    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Unable to start Athena query: {exc}"
        ) from exc

    query_id = response["QueryExecutionId"]

    print(
        f"Athena query started: {query_id}",
        flush=True,
    )

    deadline = time.monotonic() + timeout_seconds

    # --------------------------------------------------------
    # WAIT FOR QUERY COMPLETION
    # --------------------------------------------------------

    try:
        while True:

            execution = client.get_query_execution(
                QueryExecutionId=query_id,
            )

            status = execution["QueryExecution"]["Status"]

            state = status["State"]

            if state == "SUCCEEDED":
                print(
                    f"Athena query {query_id}: SUCCEEDED",
                    flush=True,
                )
                break

            if state in ("FAILED", "CANCELLED"):

                reason = status.get(
                    "StateChangeReason",
                    "No reason provided",
                )

                raise RuntimeError(
                    f"Athena query {query_id} "
                    f"{state}: {reason}"
                )

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Athena query {query_id} did not reach "
                    f"SUCCEEDED within {timeout_seconds} seconds. "
                    "Check its final status in AWS."
                )

            time.sleep(2)

    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(
            f"Unable to monitor Athena query {query_id}: "
            f"{exc}"
        ) from exc

    # --------------------------------------------------------
    # RETRIEVE ALL QUERY RESULT PAGES
    # --------------------------------------------------------

    paginator = client.get_paginator("get_query_results")
    pages = paginator.paginate(
        QueryExecutionId=query_id,
        PaginationConfig={"PageSize": 1000},
    )

    columns = []
    records = []
    first_row_seen = False

    for page in pages:
        result_set = page["ResultSet"]

        if not columns:
            columns = [
                col["Name"]
                for col in result_set[
                    "ResultSetMetadata"
                ]["ColumnInfo"]
            ]

        for row in result_set.get("Rows", []):
            values = [
                cell.get("VarCharValue")
                for cell in row.get("Data", [])
            ]

            values.extend(
                [None] * max(0, len(columns) - len(values))
            )
            values = values[:len(columns)]

            if not first_row_seen:
                first_row_seen = True

                # Skip the column header only if present.
                if values == columns:
                    continue

            records.append(values)

    result = pd.DataFrame(
        records,
        columns=columns,
    )

    print(
        f"Athena returned {len(result)} rows",
        flush=True,
    )

    return result

    # --------------------------------------------------------
    # BUILD DATAFRAME
    # IMPORTANT: Outside both pagination loops.
    # --------------------------------------------------------

    result = pd.DataFrame(
        records,
        columns=columns,
    )

    print(
        f"Athena returned {len(result)} rows",
        flush=True,
    )

    return result


# ============================================================
# CONNECTION TEST
# ============================================================

def test_connection():
    """Test connectivity to the AegisData Gold database."""

    return run_query(
        """
        -- AegisData AI customer validation
        SELECT COUNT(*) AS total_customers
        FROM aegisdata_gold_db.dim_customer_segments
        """
    )


# ============================================================
# VALIDATION TESTS
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("AEGISDATA AI - ATHENA CONNECTION TEST")
    print("=" * 60)

    # --------------------------------------------------------
    # TEST 1: SQL NORMALIZATION
    # --------------------------------------------------------

    print("\n[1] Testing SQL normalization...")

    normalized = normalize_sql(
        """
        -- Executive dashboard query
        /* Read-only validation */
        SELECT COUNT(*) AS total_customers
        FROM aegisdata_gold_db.dim_customer_segments;
        """
    )

    assert normalized.upper().startswith("SELECT")

    print("[SUCCESS] SQL normalization passed.")

    # --------------------------------------------------------
    # TEST 2: CUSTOMER COUNT
    # --------------------------------------------------------

    print("\n[2] Testing AWS Athena connection...")

    customers = test_connection()

    print(
        customers.to_string(index=False)
    )

    assert not customers.empty, (
        "Customer query returned no records."
    )

    customer_count = int(
        customers.iloc[0]["total_customers"]
    )

    assert customer_count == 5000, (
        f"Expected 5000 customers, "
        f"received {customer_count}."
    )

    print(
        "[SUCCESS] Customer validation passed."
    )

    # --------------------------------------------------------
    # TEST 3: DAILY SALES PAGINATION
    # --------------------------------------------------------

    print("\n[3] Testing complete Athena pagination...")

    sales = run_query(
        """
        -- Retrieve complete daily sales dataset
        SELECT *
        FROM aegisdata_gold_db.fact_daily_sales
        """
    )

    sales_count = len(sales)

    print(
        f"Daily sales rows: {sales_count}"
    )

    assert sales_count == 644, (
        f"Expected 644 daily sales rows, "
        f"received {sales_count}."
    )

    print(
        "[SUCCESS] Athena pagination validation passed."
    )

    # --------------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("[SUCCESS] ALL ATHENA VALIDATION CHECKS PASSED")
    print("=" * 60)
