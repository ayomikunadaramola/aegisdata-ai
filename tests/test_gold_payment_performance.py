"""Integration tests for the Gold v4 payment performance mart."""

import json
from pathlib import Path

import pytest
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DecimalType

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'processed'


def latest_run(layer, datasets):
    root = ROOT / layer
    for folder in sorted(root.glob('run_id=*'), reverse=True):
        if (folder / 'manifest.json').is_file() and all((folder / d).is_dir() for d in datasets):
            return folder
    pytest.fail(f'No complete {layer} run containing {datasets}')


@pytest.fixture(scope='module')
def spark():
    session = (SparkSession.builder.master('local[2]')
               .appName('AegisData-Gold-Payment-Tests')
               .config('spark.sql.shuffle.partitions', '4')
               .config('spark.sql.session.timeZone', 'UTC')
               .getOrCreate())
    session.sparkContext.setLogLevel('ERROR')
    yield session
    session.stop()


@pytest.fixture(scope='module')
def tables(spark):
    silver = latest_run('silver', ['payments'])
    gold = latest_run('gold', ['fact_payment_performance', 'fact_daily_sales'])
    payments = spark.read.parquet(str(silver / 'payments')).cache()
    performance = spark.read.parquet(str(gold / 'fact_payment_performance')).cache()
    daily = spark.read.parquet(str(gold / 'fact_daily_sales')).cache()
    yield payments, performance, daily, gold
    payments.unpersist()
    performance.unpersist()
    daily.unpersist()


def test_required_columns_and_types(tables):
    _, result, _, _ = tables
    needed = {'payment_date_utc', 'payment_method', 'total_transactions',
              'successful_transactions', 'failed_transactions', 'pending_transactions',
              'total_attempted_amount', 'successful_payment_amount',
              'failed_payment_amount', 'pending_payment_amount', 'payment_success_rate_pct'}
    assert needed.issubset(result.columns)
    assert isinstance(result.schema['successful_payment_amount'].dataType, DecimalType)


def test_unique_date_method_groups(tables):
    _, result, _, _ = tables
    assert result.filter('payment_date_utc IS NULL OR payment_method IS NULL').count() == 0
    assert result.select('payment_date_utc', 'payment_method').distinct().count() == result.count()


def test_transaction_status_reconciliation(tables):
    payments, result, _, _ = tables
    totals = result.agg(*[F.sum(c).alias(c) for c in (
        'total_transactions', 'successful_transactions', 'failed_transactions', 'pending_transactions')]).first()
    assert totals['total_transactions'] == payments.count()
    for status, column in [('Successful', 'successful_transactions'),
                           ('Failed', 'failed_transactions'), ('Pending', 'pending_transactions')]:
        assert totals[column] == payments.filter(F.col('payment_status') == status).count()
    assert totals['total_transactions'] == sum(totals[x] for x in (
        'successful_transactions', 'failed_transactions', 'pending_transactions'))


def test_amount_reconciliation(tables):
    payments, result, daily, _ = tables
    source = payments.agg(F.sum('payment_amount')).first()[0]
    attempted = result.agg(F.sum('total_attempted_amount')).first()[0]
    paid = result.agg(F.sum('successful_payment_amount')).first()[0]
    daily_paid = daily.agg(F.sum('paid_sales_amount')).first()[0]
    assert abs(source - attempted) <= 0.01
    assert abs(paid - daily_paid) <= 0.01


def test_success_rate_and_nonnegative_metrics(tables):
    _, result, _, _ = tables
    assert result.filter('total_transactions <= 0').count() == 0
    assert result.filter('payment_success_rate_pct < 0 OR payment_success_rate_pct > 100').count() == 0
    invalid = result.filter(F.abs(
        F.col('payment_success_rate_pct') -
        F.round(F.col('successful_transactions') * 100 / F.col('total_transactions'), 2)
    ) > 0.001)
    assert invalid.count() == 0
    assert result.filter('total_attempted_amount < 0 OR successful_payment_amount < 0 OR failed_payment_amount < 0 OR pending_payment_amount < 0').count() == 0


def test_manifest_includes_payment_model(tables):
    _, result, _, gold = tables
    manifest = json.loads((gold / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest.get('status') == 'validated'
    assert manifest['tables']['fact_payment_performance']['rows'] == result.count()
