import os
import json
import browser_cookie3

PROFILE_DIRS = {
    "darwin": os.path.expanduser("~/Library/Application Support/Google/Chrome/"),
    "linux": os.path.expanduser("~/.config/google-chrome/"),
    "win32": os.path.expandvars(r"%LOCALAPPDATA%\\Google\\Chrome\\User Data"),
}

# Нужные куки для yt-dlp
NEEDED_COOKIES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "SIDCC", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS"
}

def get_profile_name(profile_path, folder_name):
    prefs_path = os.path.join(profile_path, "Preferences")
    if os.path.exists(prefs_path):
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            return prefs.get("profile", {}).get("name", folder_name)
        except Exception:
            return folder_name
    return folder_name

def export_profile(profile_path, profile_name):
    try:
        cj = browser_cookie3.chrome(
            cookie_file=os.path.join(profile_path, "Cookies"),
            domain_name="youtube.com"
        )
    except Exception as e:
        print(f"⚠️ Пропускаю {profile_name}: {e}")
        return

    safe_name = profile_name.replace(" ", "_")
    filename = f"cookies_{safe_name}_env.txt"

    cookies_lines = [
        "# Netscape HTTP Cookie File",
        "# This file was exported for yt-dlp",
        ""  # пустая строка после шапки
    ]

    for c in cj:
        if c.name not in NEEDED_COOKIES:
            continue
        expires = str(int(c.expires)) if c.expires else "0"
        line = "\t".join([
            c.domain,
            "TRUE" if c.domain.startswith(".") else "FALSE",
            c.path,
            "TRUE" if c.secure else "FALSE",
            expires,
            c.name,
            c.value,
        ])
        cookies_lines.append(line)

    if len(cookies_lines) <= 3:
        print(f"⚠️ Нет подходящих куков для профиля {profile_name}")
        return

    # делаем одну строку с \n для .env
    cookies_str = "\\n".join(cookies_lines)

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f'YOUTUBE_COOKIES_CONTENT="{cookies_str}"\n')

    print(f"✅ Cookies для профиля {profile_name} сохранены в {filename}")

def main():
    import sys
    plat = sys.platform
    if plat not in PROFILE_DIRS:
        print("❌ Эта ОС пока не поддерживается напрямую.")
        return

    base_path = PROFILE_DIRS[plat]
    if not os.path.exists(base_path):
        print("❌ Не найден путь к профилям:", base_path)
        return

    for item in os.listdir(base_path):
        profile_path = os.path.join(base_path, item)
        if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, "Cookies")):
            profile_name = get_profile_name(profile_path, item)
            export_profile(profile_path, profile_name)

if __name__ == "__main__":
    main()
