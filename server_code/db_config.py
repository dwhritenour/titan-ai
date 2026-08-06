import anvil.server
import anvil.secrets
import pymssql

# Read-only connection to the Nexus SQL Server database.
#
# Credentials live in App Secrets (Settings -> Secrets), NOT in code:
#   NEXUS_SERVER, NEXUS_DATABASE, NEXUS_USERNAME, NEXUS_PASSWORD
#
# There is no Anthropic key in this app. The AI itself runs in titan-ai-service
# on Hermes; this app only queues questions and reads back answers.


def _nexus_config():
    return {
        "server": anvil.secrets.get_secret("NEXUS_SERVER"),
        "database": anvil.secrets.get_secret("NEXUS_DATABASE"),
        "user": anvil.secrets.get_secret("NEXUS_USERNAME"),
        "password": anvil.secrets.get_secret("NEXUS_PASSWORD"),
    }


def get_connection():
    """Open a pymssql connection to Nexus. Callers should close it (use `with`)."""
    cfg = _nexus_config()
    return pymssql.connect(
        server=cfg["server"],
        database=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        as_dict=True,
        timeout=30,
        login_timeout=30,
    )


@anvil.server.callable(require_user=True)
def test_nexus_connection():
    """Quick sanity check. Run from the Server Console during setup (checklist step 2)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DB_NAME() AS db_name, SUSER_SNAME() AS login_name, GETDATE() AS server_time"
        )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    return {
        "ok": True,
        "database": row["db_name"],
        "login": row["login_name"],
        "server_time": str(row["server_time"]),
    }
