def pytest_addoption(parser):
    parser.addoption("--keep-cfn", action="store_true", default=False)
    parser.addoption("--stack-name", action="store", default=None)
    parser.addoption("--region", action="store", default="us-east-1")
    parser.addoption("--ami", action="store", default="ami-0791dd37ca6da66c5")
