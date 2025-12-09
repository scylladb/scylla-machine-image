.PHONY: help install install-dev sync test test-unit test-integration test-validation test-cfn clean format lint check

# Default target
help:
	@echo "Scylla Machine Image - Makefile Commands"
	@echo "========================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install          Install dependencies using uv"
	@echo "  make install-dev      Install all dependencies including dev tools"
	@echo "  make sync             Sync dependencies from lock file"
	@echo ""
	@echo "Testing:"
	@echo "  make test             Run all tests (excluding integration)"
	@echo "  make test-unit        Run unit tests only"
	@echo "  make test-validation  Run validation tests (CloudFormation)"
	@echo "  make test-integration Run integration tests (requires AWS creds)"
	@echo "  make test-cfn         Run CloudFormation tests"
	@echo "  make test-all         Run ALL tests including integration"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format           Format code with black and ruff"
	@echo "  make lint             Run linters (ruff)"
	@echo "  make check            Run all checks (lint + type check)"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean            Remove cache and temporary files"
	@echo ""
	@echo "Environment Variables:"
	@echo "  AWS_REGION=us-east-1"
	@echo "  RUN_CFN_INTEGRATION_TESTS=1  (enable integration tests)"

# Installation targets
install:
	uv sync --extra test --extra aws

install-dev:
	uv sync --all-extras

sync:
	uv sync

# Testing targets
test:
	uv run pytest tests/ -m "not integration" -v

test-unit:
	uv run pytest tests/ -m "unit" -v

test-validation:
	uv run pytest tests/test_cloudformation.py -m "validation" -v

test-integration:
	@echo "⚠️  WARNING: This will create AWS resources that may incur costs!"
	@sleep 2
	RUN_CFN_INTEGRATION_TESTS=1 uv run pytest tests/ -m "integration" -v -s

test-cfn:
	uv run pytest tests/test_cloudformation.py -v

test-all:
	@echo "⚠️  WARNING: This will create AWS resources that may incur costs!"
	@sleep 2
	RUN_CFN_INTEGRATION_TESTS=1 uv run pytest tests/ -v -s

# Code quality targets
format:
	uv run ruff format lib/ tests/ tools/
	uv run ruff check --fix lib/ tests/ tools/

lint:
	uv run ruff check lib/ tests/ tools/

type-check:
	uv run mypy lib/ --ignore-missing-imports

check: lint type-check
	@echo "✅ All checks passed!"

# Cleanup targets
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf build/ dist/ htmlcov/ .coverage 2>/dev/null || true
	@echo "✅ Cleanup complete!"

# UV-specific targets
uv-lock:
	uv lock

uv-update:
	uv lock --upgrade

uv-add:
	@read -p "Package name: " pkg; \
	uv add $$pkg

uv-add-dev:
	@read -p "Package name: " pkg; \
	uv add --dev $$pkg

# Quick test runners for specific scenarios
quick-test:
	uv run pytest tests/ -m "not slow and not integration" -v

watch-test:
	uv run pytest-watch tests/ -m "not integration"

# CloudFormation specific
cfn-validate:
	uv run pytest tests/test_cloudformation.py::TestCloudFormationTemplate -v

cfn-integration:
	@echo "⚠️  Creating AWS CloudFormation stack for testing..."
	@sleep 2
	RUN_CFN_INTEGRATION_TESTS=1 uv run pytest tests/test_cloudformation.py::TestCloudFormationDeployment -v -s

cfn-example:
	uv run python tests/example_cfn_usage.py

# Coverage
coverage:
	uv run pytest tests/ --cov=lib --cov-report=html --cov-report=term -m "not integration"
	@echo "Coverage report generated in htmlcov/index.html"

# Build
build:
	uv build

# Run specific test
run-test:
	@read -p "Test path (e.g., tests/test_file.py::test_name): " test_path; \
	uv run pytest $$test_path -v -s
