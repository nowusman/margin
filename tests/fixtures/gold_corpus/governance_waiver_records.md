# WAIVER-0033: Temporary Exemption from Strict Mutual TLS for Legacy Internal Services

Record Type: waiver
ID: waiver-0033
Owner: Security Lead
Status: approved
Domain: platform
Valid From: 2026-01-01
Valid To: 2026-12-31

## Justification

Legacy accounting services cannot support TLS 1.3 certificates before the planned Q4 service mesh cutover.

## Approved Scope

Restricted to inter-service traffic between Order Service and Legacy Billing Host in VPC-Private-02.

## Conditions

Service account tokens and network security group isolation must be maintained until certificate renewal.

## Audit Events

- 2026-01-01: Submitted by user-lead-02
- 2026-01-05: Approved by user-ciso-01 with mandatory expiration date 2026-12-31
