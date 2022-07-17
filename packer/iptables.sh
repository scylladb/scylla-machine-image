#!/bin/bash

# Flush IPtables rules
iptables -F
# Ensure default deny firewall policy
iptables -P INPUT DROP
iptables -P OUTPUT DROP
iptables -P FORWARD DROP
# Ensure loopback traffic is configured
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT -s 127.0.0.0/8 -j DROP
# Ensure outbound and established connections are configured
iptables -A OUTPUT -p tcp -m state --state NEW,ESTABLISHED -j ACCEPT
iptables -A OUTPUT -p udp -m state --state NEW,ESTABLISHED -j ACCEPT
iptables -A OUTPUT -p icmp -m state --state NEW,ESTABLISHED -j ACCEPT
iptables -A INPUT -p tcp -m state --state ESTABLISHED -j ACCEPT
iptables -A INPUT -p udp -m state --state ESTABLISHED -j ACCEPT
iptables -A INPUT -p icmp -m state --state ESTABLISHED -j ACCEPT
# Open inbound ssh(tcp port 22) connections
iptables -A INPUT -p tcp --dport 22 -m state --state NEW -j ACCEPT
# Open inbound Scylla connections
iptables -A INPUT -p tcp --dport 9042 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 7000 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 7001 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 7199 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 10000 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 9180 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 9100 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 9160 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 19042 -m state --state NEW -j ACCEPT
iptables -A INPUT -p tcp --dport 19142 -m state --state NEW -j ACCEPT

# Flush ip6tables rules
ip6tables -F
# Ensure default deny firewall policy
ip6tables -P INPUT DROP
ip6tables -P OUTPUT DROP
ip6tables -P FORWARD DROP
# Ensure loopback traffic is configured
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A OUTPUT -o lo -j ACCEPT
ip6tables -A INPUT -s ::1 -j DROP
# Ensure outbound and established connections are configured
ip6tables -A OUTPUT -p tcp -m state --state NEW,ESTABLISHED -j ACCEPT
ip6tables -A OUTPUT -p udp -m state --state NEW,ESTABLISHED -j ACCEPT
ip6tables -A OUTPUT -p icmp -m state --state NEW,ESTABLISHED -j ACCEPT
ip6tables -A INPUT -p tcp -m state --state ESTABLISHED -j ACCEPT
ip6tables -A INPUT -p udp -m state --state ESTABLISHED -j ACCEPT
ip6tables -A INPUT -p icmp -m state --state ESTABLISHED -j ACCEPT
# Open inbound ssh(tcp port 22) connections
ip6tables -A INPUT -p tcp --dport 22 -m state --state NEW -j ACCEPT
# Open inbound Scylla connections
ip6tables -A INPUT -p tcp --dport 9042 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 7000 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 7001 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 7199 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 10000 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 9180 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 9100 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 9160 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 19042 -m state --state NEW -j ACCEPT
ip6tables -A INPUT -p tcp --dport 19142 -m state --state NEW -j ACCEPT
