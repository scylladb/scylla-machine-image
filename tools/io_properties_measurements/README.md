# Scylla IO Properties Retriever

This Python script launches AWS EC2 instances with a specified AMI and instance type(s), waits for them to boot up, connects via SSH, and retrieves the contents of `/etc/scylla.d/io_properties.yaml` if available (waiting up to 30 minutes for the file to appear).

## Features

- **Single Instance Processing**: Launch and process individual instance types
- **Family Processing**: Automatically detect and process all instance types in a family (e.g., `i8g` → `i8g.large`, `i8g.xlarge`, etc.)
- **Parallel Processing**: Process multiple instances concurrently to speed up data collection
- **Cost Estimation**: Preview estimated AWS costs before launching instances
- **Configuration Files**: Save and load settings from YAML configuration files
- **Progress Indicators**: Real-time progress tracking for long-running operations
- **Human-Readable Output**: Always displays IO properties in easy-to-read table format
- **Dry Run Mode**: Preview operations without launching actual instances
- **Error Recovery**: Robust error handling and retry mechanisms

## Prerequisites

- AWS credentials configured (e.g., via AWS CLI or environment variables)
- An SSH key pair created in AWS EC2
- The private key file saved locally
- A security group that allows SSH (port 22) inbound traffic from your IP address or 0.0.0.0/0 (default: 'sg-088128b2712c264d1')

## Installation

### Using uv 
From the root of the scylla-machine-image repository:

```bash
# Install all dependencies including io-properties tools
uv sync --extra io-properties

# Or install as part of dev dependencies
uv sync --extra dev
```


## Usage

```
python get_scylla_io_properties.py --ami <AMI_ID> --instance-type <INSTANCE_TYPE> --key-path <PATH_TO_PRIVATE_KEY>
```

### Arguments

- `--ami`: The AMI ID to use for the instance
- `--instance-type`: Instance type (e.g., i8g.2xlarge) or family (e.g., i8g for all i8g instances) - hyphens will be converted to dots automatically
- `--key-path`: Path to your private SSH key file (required)
- `--key-name`: Name of the key pair in AWS (optional, defaults to basename of key file without extension)
- `--security-group`: Security group name or ID (default: 'sg-088128b2712c264d1')
- `--subnet-id`: Subnet ID (optional, auto-detected from security group VPC if not specified)
- `--username`: SSH username (default: scyllaadm)
- `--region`: AWS region to use (default: us-east-1)
- `--parallel`: Number of instances to process in parallel (default: 1)
- `--dry-run`: Show what would be done without launching instances (useful for families)
- `--max-instances`: Maximum number of instances to process for a family (default: no limit)
- `--override`: Force re-running scylla_io_setup by removing existing io_properties.yaml file
- `--update-aws-params`: Update aws_io_params.yaml with collected IO properties
- `--aws-params-file`: Path to aws_io_params.yaml file (default: ../../common/aws_io_params.yaml)
- `--config`: Configuration file to load/save settings (default: config.yaml)
- `--save-config`: Save current settings to config file

## Configuration Files

You can save your settings to a configuration file for reuse:

```bash
# Save current settings
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g --key-path ~/.ssh/my-key.pem --save-config

# Use saved configuration
python get_scylla_io_properties.py --instance-type i8g.2xlarge
```

See `config.yaml` for an example configuration file.

## Examples

### Single Instance Type
```bash
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g.2xlarge --key-path ~/.ssh/my-key.pem
```

### Instance Family (All Types)
```bash
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g --key-path ~/.ssh/my-key.pem
```

### Parallel Processing
```bash
# Process up to 3 instances concurrently
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g --key-path ~/.ssh/my-key.pem --parallel 3
```

### Force IO Properties Regeneration
```bash
# Use --override to force re-running scylla_io_setup and generate fresh IO properties
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g.2xlarge --key-path ~/.ssh/my-key.pem --override
```

### Update AWS Parameters File
```bash
# Collect IO properties and update aws_io_params.yaml
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g --key-path ~/.ssh/my-key.pem --update-aws-params
```

### Instance Family with Limits
```bash
python get_scylla_io_properties.py --ami ami-12345678 --instance-type i8g --key-path ~/.ssh/my-key.pem --max-instances 3 --dry-run
```

### Using Configuration File
```bash
# Load settings from config file
python get_scylla_io_properties.py --config my-config.yaml --instance-type i8g.large

# Save current command line settings to config
python get_scylla_io_properties.py --ami ami-12345678 --key-path ~/.ssh/my-key.pem --save-config
```

## Parallel Processing

For processing multiple instances (families), you can use parallel execution:

- `--parallel 1`: Sequential processing (default)
- `--parallel 3`: Process up to 3 instances concurrently
- Automatically handles dependencies and resource conflicts
- Shows progress indicators during parallel execution

**Note:** Parallel processing can significantly speed up data collection but may increase AWS API rate limits and temporary costs.

## Output Format

The script outputs IO properties in a human-readable table format:

```
IO Properties for i8g.2xlarge:
+-----------------------+-----------------+
| Property              | Value           |
+=======================+=================+
| disks.mountpoint      | /var/lib/scylla |
+-----------------------+-----------------+
| disks.read_iops       | 400000          |
+-----------------------+-----------------+
| disks.read_bandwidth  | 2000000000      |
+-----------------------+-----------------+
| disks.write_iops      | 271696          |
+-----------------------+-----------------+
| disks.write_bandwidth | 1314000000      |
+-----------------------+-----------------+
```

**Note**: YAML format is still used internally for saving IO properties to files (when using `--update-aws-params`), but the console output is always in table format for better readability.

## Override Flag

The `--override` flag provides a way to force fresh IO property measurements by:

1. **Removing existing IO properties**: Deletes `/etc/scylla.d/io_properties.yaml` if it exists
2. **Running scylla_io_setup**: Executes `scylla_io_setup` to perform actual disk benchmarking
3. **Generating new measurements**: Creates new IO properties based on real-time disk performance

This is useful when:
- You want fresh measurements instead of cached/pre-configured values
- The instance type is new and not in the pre-configured parameters
- You suspect the existing IO properties may be inaccurate
- You want to verify that measurements match expected performance

**Note**: Using `--override` will significantly increase processing time as it performs actual disk benchmarking on each instance.

## Notes

- Make sure your security group allows SSH (port 22) inbound traffic
- The instance will be terminated after retrieving the properties  
- If the file doesn't appear within the configured timeout, the script will fail
- When processing instance families, instances are launched and terminated sequentially by default
- Use `--dry-run` to preview which instance types would be processed without incurring charges
- The `--override` flag forces actual disk benchmarking which takes significantly longer than reading pre-configured values
- For large families, consider using `--max-instances` to limit processing
- Configuration files help maintain consistent settings across runs
- Progress indicators show real-time status for long-running operations
