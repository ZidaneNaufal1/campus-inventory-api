# Design decisions and evidence

## Problem

Campus labs need a record of equipment and active loans. This local MVP demonstrates the core lending workflow with synthetic data before any user-facing deployment. It intentionally does not collect real student information or claim an existing campus pilot.

## SQLite and concurrency

Each active loan represents one unit. The database stores the total quantity on an asset; available stock is total minus active loans. A partial unique index prevents an active duplicate for one borrower and asset. `BEGIN IMMEDIATE` serializes writers before checking available stock, and insert/return happen inside the same transaction. A concurrent local HTTP test checks two different borrowers competing for one remaining unit: one gets 201, one 409. A database trigger test aborts insert and checks no loan remains and stock is unchanged.

These tests cover SQLite on the tested environments. They do not establish distributed consistency, heavy-load performance, or protection against hostile database files. The API accepts only operator-controlled local data; its database is not encrypted.

## Local verification on 2026-10-27

Windows CPython 3.14: `python -m unittest -v` ran 14 tests, 13 passed and one symlink check was skipped due to OS permission. The local demo passed. Tests cover competing loans, duplicate loans, exhausted stock, multiple units, return/repeated return, query filters, malformed input, missing references, insert rollback, OpenAPI endpoint, foreign database preservation and schema version.

The first test run incorrectly assumed every deeply nested JSON array must be rejected by Python's parser; Python 3.14 accepted the syntactically valid input. The incorrect assertion was removed. This was a test correction, not a claimed runtime fix. Endpoint validation still requires the documented object fields and catches recursion/parser failures. No performance or security audit is claimed.

## Learning path

Read `inventory.main → make_server → Handler → borrow/return_loan → SQLite transaction`. Explain why the stock check and insert must share a lock, why a duplicate borrower receives 409, and why the local MVP cannot yet be used as a public writable service. This is a learning guide, not a claim that the maintainer has completed the review.
