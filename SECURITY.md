# Security reports and publication

Do not put credentials, pairing codes, watch keys, personal addresses, entity
IDs, HA configuration exports, or unredacted logs in public issues or pull
requests. Use synthetic examples when reporting a problem.

For a vulnerability, use GitHub's **Security → Report a vulnerability** on this
repository when available. If private reporting is unavailable, open an issue
asking for a private reporting channel without including exploit details or
private data. Never post a live credential to demonstrate a problem.

## Publishing safely

Publish tracked source through Git rather than uploading a workspace or a Home
Assistant configuration directory. In particular, `.storage/fan_control` contains
live watch credentials. Protect it and HA backups; neither belongs in this repo.
Ignored files remain on disk, and ignore rules do not remove committed files.

Optional push relay settings add a separate shared secret and APNs registration
tokens to that protected HA storage. The relay's Apple .p8 key, config.php, provider
JWT cache and SQLite database must stay outside every public document root and
outside Git. The example configuration is disabled and contains no credentials.
The fixed HTTPS relay URL rejects redirects; all signed requests bind the exact
body, timestamp and single-use nonce. The relay cannot access HA or dispatch
vehicle commands. Revocation stops new hints immediately in HA; remote token
deletion is best effort when the relay is unavailable.

Before publishing, inspect staged files, all branches and tags, commit metadata,
and any releases or attached artifacts. Scan history as well as the current
tree. Revoke any exposed credential first; deleting it from a later commit does
not remove it from history or existing copies. Review new changes again before
publishing them.

Repository owner names, commit metadata and activity are visible in a public
repository. A source review does not cover GitHub account settings, collaborators,
Actions secrets, release downloads, issues or other remotely stored content.
