# Setup Guide for UV-based Development

This guide will help you set up the development environment using UV.

## Prerequisites

- Python 3.9 or higher
- Git
- (Optional) AWS CLI for CloudFormation testing

## Step-by-Step Setup

### 1. Install UV

Choose one of the following methods:

#### Method A: Using the installer script (Recommended)

```bash
# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

#### Method B: Using pip

```bash
pip install uv
```

#### Method C: Using pipx

```bash
pipx install uv
```

### 2. Clone the Repository

```bash
git clone https://github.com/scylladb/scylla-cloud-image.git
cd scylla-cloud-image
```

### 3. Install Dependencies

```bash
# Install base dependencies + test dependencies
uv sync --extra test --extra aws

# OR install all dependencies including dev tools
uv sync --all-extras
```

This will:
- Create a virtual environment in `.venv/`
- Install all dependencies from `pyproject.toml`
- Generate/update `uv.lock` file

### 4. Verify Installation

```bash
# Run a simple test
uv run pytest tests/test_cloudformation.py::TestCloudFormationTemplate::test_template_exists -v

# Check installed packages
uv pip list
```

## Running Tests

### Quick Validation (No AWS Resources)

```bash
# Using UV
uv run pytest tests/test_cloudformation.py -m validation -v

# Using Make
make test-validation
```

### All Tests (Excluding Integration)

```bash
# Using UV
uv run pytest tests/ -m "not integration" -v

# Using Make
make test
```

### Integration Tests (Creates AWS Resources)

```bash
# Configure AWS credentials first
export AWS_ACCESS_KEY_ID=your_access_key_id
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=us-east-1

# Enable integration tests
export RUN_CFN_INTEGRATION_TESTS=1

# Run integration tests
uv run pytest tests/test_cloudformation.py -m integration -v -s

# OR using Make (with warning)
make test-integration
```

## Development Workflow

### Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/your-feature
   ```

2. **Make your changes**
   ```bash
   # Edit files...
   ```

3. **Run tests**
   ```bash
   # Quick tests
   uv run pytest tests/ -m "not integration" -v
   
   # Or specific test
   uv run pytest tests/test_cloudformation.py::TestCloudFormationTemplate -v
   ```

4. **Format code**
   ```bash
   make format
   
   # Or manually
   uv run ruff format lib/ tests/ tools/
   uv run ruff check --fix lib/ tests/ tools/
   ```

5. **Check code quality**
   ```bash
   make check
   
   # Or manually
   uv run ruff check lib/ tests/ tools/
   uv run mypy lib/ --ignore-missing-imports
   ```

6. **Commit and push**
   ```bash
   git add .
   git commit -m "Your descriptive commit message"
   git push origin feature/your-feature
   ```

### Adding Dependencies

```bash
# Add a runtime dependency
uv add package-name

# Add a development dependency
uv add --dev package-name

# Add to specific optional group
# (edit pyproject.toml manually and run):
uv lock
uv sync
```

## Common Tasks

### Run Specific Test

```bash
# By test name
uv run pytest tests/test_cloudformation.py::TestCloudFormationTemplate::test_template_exists -v

# By marker
uv run pytest tests/ -m validation -v

# By keyword
uv run pytest tests/ -k "cloudformation" -v
```

### Run Tests with Coverage

```bash
# Using Make
make coverage

# Or manually
uv run pytest tests/ --cov=lib --cov-report=html --cov-report=term -m "not integration"

# View coverage report
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

### Update Dependencies

```bash
# Update all dependencies
uv lock --upgrade

# Re-sync
uv sync
```

### Clean Build Artifacts

```bash
# Using Make
make clean

# Or manually
find . -type d -name "__pycache__" -exec rm -rf {} +
find . -type d -name ".pytest_cache" -exec rm -rf {} +
```

## IDE Setup

### VSCode

1. **Install Python extension**
   - Install "Python" extension by Microsoft

2. **Select interpreter**
   - Press `Cmd/Ctrl + Shift + P`
   - Type "Python: Select Interpreter"
   - Choose `.venv/bin/python`

3. **Recommended settings** (`.vscode/settings.json`):
   ```json
   {
     "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
     "python.testing.pytestEnabled": true,
     "python.testing.pytestArgs": [
       "tests",
       "-v"
     ],
     "[python]": {
       "editor.formatOnSave": true,
       "editor.defaultFormatter": "charliermarsh.ruff"
     }
   }
   ```

### PyCharm

1. **Configure interpreter**
   - File → Settings → Project → Python Interpreter
   - Click gear icon → Add
   - Select "Existing environment"
   - Choose `.venv/bin/python`

2. **Configure pytest**
   - File → Settings → Tools → Python Integrated Tools
   - Set "Default test runner" to "pytest"

## Troubleshooting

### "uv: command not found"

```bash
# Ensure UV is in your PATH
export PATH="$HOME/.local/bin:$PATH"

# Add to your shell profile
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc  # or ~/.zshrc
source ~/.bashrc  # or ~/.zshrc
```

### "No lockfile found"

```bash
# Generate lockfile
uv lock
```

### "Python version mismatch"

```bash
# Use specific Python version
uv venv --python 3.12
uv sync
```

### "Import errors in tests"

```bash
# Ensure dependencies are installed
uv sync --extra test --extra aws

# Verify virtual environment is activated
which python
# Should show: /path/to/project/.venv/bin/python
```

### "AWS credentials not configured"

```bash
# Option 1: Environment variables
export AWS_ACCESS_KEY_ID=your_key_id
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=us-east-1

# Option 2: AWS CLI configuration
aws configure

# Option 3: AWS credentials file
cat > ~/.aws/credentials << EOF
[default]
aws_access_key_id = your_key_id
aws_secret_access_key = your_secret_key
EOF
```

## Makefile Reference

All available Make commands:

```bash
make help              # Show all available commands
make install           # Install dependencies
make install-dev       # Install all dependencies including dev tools
make test              # Run all tests (excluding integration)
make test-validation   # Run validation tests
make test-integration  # Run integration tests (with AWS)
make format            # Format code
make lint              # Run linters
make check             # Run all checks
make clean             # Clean temporary files
```

## UV Command Reference

Common UV commands:

```bash
uv sync                    # Install dependencies
uv sync --extra test       # Install with specific extra
uv sync --all-extras       # Install all optional dependencies
uv add package             # Add a dependency
uv add --dev package       # Add a dev dependency
uv remove package          # Remove a dependency
uv lock                    # Generate/update lock file
uv lock --upgrade          # Update dependencies
uv run pytest              # Run command in venv
uv pip list                # List installed packages
uv pip show package        # Show package info
uv venv                    # Create virtual environment
```

## Next Steps

1. ✅ Complete setup (you're here!)
2. Run validation tests: `make test-validation`
3. Read the CloudFormation test docs: `tests/README_CFN_TESTS.md`
4. Explore example usage: `uv run python tests/example_cfn_usage.py`
5. Run integration tests: `make test-integration` (requires AWS)
6. Start developing!

## Resources

- [UV Documentation](https://github.com/astral-sh/uv)
- [Project README](../README.md)
- [CloudFormation Testing Guide](tests/README_CFN_TESTS.md)
- [Quick Start Guide](tests/QUICKSTART_CFN_TESTS.md)
- [UV Usage Guide](UV_GUIDE.md)
