"""Quick test — list today's events from Sonal's calendar using the service account."""

from datetime import datetime
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build

IST = ZoneInfo("Asia/Kolkata")

SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "project_id": "testing-one-490804",
    "private_key_id": "3b3b45ea48f2d00c9ba7d66b96daef8d89bed1fe",
    "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCfxG+FTRepalQ1\nPx41mlc51UiGS2YMjCWIDLt/G1F/KSmzUwdO3mqso4NBlw676ZvqPZG8nXeiGbgf\nRpfx3NuGTkn6KExLbOn4lvLZNTtijqUT61XR7eGfg+fl434Jylh4jzNudnp60rhs\n5LpuVx17zRij1R5ZBEdSjgGv0CKNgZ5Hq2X7RZSo8GpSUNPWUOAjTRN154ykFpnJ\nYQriUVWG63cYf2KxX2HMVUMLdqSD/iX4j4eGipz1y2BtdRVxGv4MJduhvAzTckKR\nKvAB8RWzSCoiwLrtQQmenauSkd/2+cDTJKH4UwwKBePiayNbxtimlVGCTbWwOnh/\nPn0+ot1jAgMBAAECggEAARKzLbcvq6ofUv+PsYwjU4FHYZYxDcWFMlg6NtIqpnV9\ne7WuN2KOtIZ2nmKoLS1A/ajt3nvvmULzSyEmFRrxeIH0b5MvYVROb/vSOWY+S1z/\nkSuD9zXR+Eh3rsIWjx9GSoJVC0QNcjS8cLLKk8IkXGKrbIeaaBOmHPuqLubxdyIp\n2U6BcNT3MEHrCheDTsBYpJ5A6QU4tCFBR5kQ8XKEmi5d4dyMsi78MES3Jtbu+k7D\nLobMKSvIzWk8kcFQrAJoY4AGj5IaxdbbT+FcONQrutqgMeI0O8bpNMbOTNQoe9jI\nZ3TFmdZchGKQqrj5Fi9w9vtBjy1DWq6aaWnxYaHuOQKBgQDMtzrg1ma/tstbTblb\neAHW+O1PypJqqLvct7C3dofpZiJ3OOvIKyi8r8GaE3b1WVu/vm95oYPUT9mQHmC1\nKZj/OHLTtj2cMu8bVV1R6I8529ihI5DxN5MIUb8pgAcLunO6q4MQoIhH5gucX1+s\nBO3RE+E4wGtdnGLJ4TSP1zrKCQKBgQDHypZG6R4FL4nLD+cDgeP8fOyXZ8pjZq7t\n0KOzYHffdZ6aWURgOpjpl7zOWlYPkk9Ve8LK7qecbhao3Ks/CVPRUnDKT7ks50uF\nzJ4oaNUsxCIOqXW0lt4wyQiIS3dbjU65JeGCpHzqrGlVl/914953rCpUzEMpfBH7\nLztipiJ3CwKBgGExYR/cSx4cYEIyDZ3SxTTWLH1klM1U3RR2lc0U1oRGfHiUKsV3\nUDj9TPKk6SalTT0k4fIib99+JbIZ6ho47K3HlCTV8jxVplYY2lyICHAU463ln+wW\nUZVykkrWwQGdfVKUGX7saKeSHdMZKOgX0v0f7h8upArms7RbWsQsEHpZAoGBAKP8\nhJxve1SGUHN7+gHG+3qijw83AcfU4IASYEs7QykHQccuvhC+CASzpyU4wKrHTJa9\nnoUyniCnu7GebnCvFz0YjbuA9F0G+9Y2vRot8ctssQeX0CUKMBWa7IXya2WZ9qPB\nk/fHS0DTgyHeQLBi+JcBmT1A61+BlsC1Y+j0tBVBAoGBALq3gNgFvWKcnim7pBk7\npRZP6Ng8WC8KxYdavncxS5SykY0GJsEOW3iMvsSPxOgruA2RpYvPH8d1UqN2PXVr\nF4qqphPfCJj0kMzDyK+lyUeB2uWPI93wchLGKU4n55n/9aPQE/ZFuoZCdLmkYYJv\nSuh61aHvwxRswl2JeaQDZMbY\n-----END PRIVATE KEY-----\n",
    "client_email": "chat-bot@testing-one-490804.iam.gserviceaccount.com",
    "client_id": "104261218426171206814",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

CALENDAR_ID = "sonal@valuecart.in"  # change if her calendar ID is different

creds = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO,
    scopes=["https://www.googleapis.com/auth/calendar.readonly"],
)
svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

now = datetime.now(IST)
day_start = now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
day_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)

print(f"Checking calendar: {CALENDAR_ID}")
print(f"Date: {now.strftime('%A, %d %B %Y')}\n")

result = svc.events().list(
    calendarId=CALENDAR_ID,
    timeMin=day_start.isoformat(),
    timeMax=day_end.isoformat(),
    singleEvents=True,
    orderBy="startTime",
).execute()

events = result.get("items", [])
if not events:
    print("No events found today.")
else:
    print(f"Found {len(events)} event(s) today:")
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date", "?"))
        print(f"  - {e.get('summary', '(no title)')} @ {start}")
