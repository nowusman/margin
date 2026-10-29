# Authentication Architecture

Architecture Decision v1:
Authentication uses monolithic session cookies stored in Redis.

Architecture Decision v2:
Authentication uses stateless signed OIDC JWT tokens verified at the gateway, superseding monolithic session cookies.
