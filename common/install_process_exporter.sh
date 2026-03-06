#!/bin/bash -e
#

# os-release may be missing in container environment by default.
if [ -f "/etc/os-release" ]; then
    . /etc/os-release
elif [ -f "/etc/arch-release" ]; then
    export ID=arch
else
    echo "/etc/os-release missing."
    exit 1
fi

go_arch() {
    local -A GO_ARCH=(
        ["x86_64"]=amd64
        ["aarch64"]=arm64
    )
    echo ${GO_ARCH["$(arch)"]}
}

PROCESS_EXPORTER_VERSION=0.8.7
declare -A PROCESS_EXPORTER_CHECKSUM=(
    ["x86_64"]=6d274cca5e94c6a25e55ec05762a472561859ce0a05b984aaedb67dd857ceee2
    ["aarch64"]=4a2502f290323e57eeeb070fc10e64047ad0cd838ae5a1b347868f75667b5ab0
)
PROCESS_EXPORTER_DIR=/opt/scylladb/dependencies

process_exporter_base_name() {
    echo "process-exporter-$PROCESS_EXPORTER_VERSION.linux-$(go_arch)"
}

process_exporter_filename() {
    echo "$(process_exporter_base_name).tar.gz"
}

process_exporter_tar_path() {
    echo "/tmp/process-exporter.tar.gz"
}

process_exporter_checksum() {
	sha256sum "$(process_exporter_tar_path)" | while read -r sum _; do [[ "$sum" == "${PROCESS_EXPORTER_CHECKSUM["$(arch)"]}" ]]; done
}

process_exporter_url() {
    echo "https://github.com/ncabatoff/process-exporter/releases/download/v$PROCESS_EXPORTER_VERSION/$(process_exporter_filename)"
}

if [ -f "$(process_exporter_tar_path)" ] && process_exporter_checksum; then
    echo "$(process_exporter_filename) already exists, skipping download"
else
    mkdir -p "$PROCESS_EXPORTER_DIR"
    curl -L -o "$(process_exporter_tar_path)" "$(process_exporter_url)"
    if ! process_exporter_checksum; then
        echo "$(process_exporter_filename) download failed"
        exit 1
    fi
fi

tar -xzf "$(process_exporter_tar_path)" --strip-components=1 -C "$PROCESS_EXPORTER_DIR" "$(process_exporter_base_name)/process-exporter"
cp process-exporter.service /usr/lib/systemd/system/
cp process-exporter.yml "$PROCESS_EXPORTER_DIR"
systemctl daemon-reload
systemctl enable process-exporter.service

