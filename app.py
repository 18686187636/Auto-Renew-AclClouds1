#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    WebDriverException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

# ----- 配置 -----
EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'

EXPIRE_LABELS = ('Expire dans', 'Expires in', 'Expire le', 'Expires on', '到期', '过期', 'EXPIRY')
SUCCESS_KEYWORDS = ('successfully', 'avec succès', 'réussi', 'succès', '成功')

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ"
_LOWER = "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"

SECTION_TITLE_RE = re.compile(
    r'^(service\s*list|services?|projects?|mes\s+services?|项目列表|项目|服务列表|列表)$',
    re.I,
)

# 表格列名映射（多语言）
COLUMN_ALIASES = {
    'model': ['model', 'modèle', 'modele'],
    'type': ['type'],
    'service_id': ['service id', 'id du service', 'id service'],
    'expiry': ['expiry', 'expiration', 'expire', 'expires'],
    'renewal': ['renewal', 'renouvellement', 'renew'],
    'status': ['status', 'statut', 'état'],
}


# ==================== 基础工具 ====================

def beijing_time_str():
    try:
        return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def send_telegram(message):
    if TG_BOT_TOKEN and TG_CHAT_ID:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {'chat_id': TG_CHAT_ID, 'text': message}
        try:
            requests.post(url, data=data, timeout=10)
            print(f"Telegram sent: {message[:50]}...")
        except Exception as e:
            print(f"Failed to send Telegram: {e}")
    else:
        print(f"[Telegram disabled] {message}")


def wait_for_url_change(sb, original_url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        current_url = sb.get_current_url()
        if current_url != original_url:
            return True
        sb.sleep(0.5)
    raise Exception(f"等待 URL 变化超时 ({timeout}秒)，当前仍为: {original_url}")


def is_login_page(sb):
    return LOGIN_PATH in sb.get_current_url()


def is_logged_in(sb):
    url = sb.get_current_url()
    return '/dashboard' in url and '/auth/login' not in url


def scroll_to_selector(sb, selector):
    sb.scroll_to(selector)
    sb.sleep(0.2)


def safe_click_element(sb, element, label):
    try:
        sb.driver.execute_script(
            'arguments[0].scrollIntoView({block: "center", inline: "center"});',
            element,
        )
        sb.sleep(0.5)
        try:
            element.click()
            return True
        except (ElementClickInterceptedException, WebDriverException, StaleElementReferenceException) as e:
            print(f"{label} 普通点击失败，改用 JavaScript 点击: {e}")
        sb.driver.execute_script('arguments[0].click();', element)
        sb.sleep(0.5)
        return True
    except StaleElementReferenceException:
        print(f"{label} 元素已失效，点击前需要重新定位")
        return False


def element_text(element):
    try:
        return element.text.strip()
    except Exception:
        return ''


def unique_elements(elements):
    unique = []
    seen_ids = set()
    for element in elements:
        element_id = getattr(element, 'id', None)
        if element_id and element_id in seen_ids:
            continue
        if element_id:
            seen_ids.add(element_id)
        unique.append(element)
    return unique


def find_elements(root, selector):
    if selector.startswith(('/', './/', '(', 'ancestor', 'descendant', 'following', 'preceding')):
        by = By.XPATH
    else:
        by = By.CSS_SELECTOR
    return root.find_elements(by, selector)


# ==================== SPA 等待 ====================

def wait_for_spa_ready(sb, timeout=20):
    """等待 React 把项目表格渲染到 DOM。"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            tables = sb.driver.find_elements(By.TAG_NAME, 'table')
            for table in tables:
                rows = table.find_elements(By.XPATH, './/tr')
                if len(rows) >= 2:
                    return True
        except Exception:
            pass
        sb.sleep(0.5)
    print("⚠️ 等待 SPA 渲染超时")
    return False


# ==================== 表格解析（核心） ====================

def _normalize_col_name(text):
    return re.sub(r'\s+', ' ', (text or '').strip()).lower()


def _match_column(header_text):
    """把表头文本映射到内部列名。"""
    normalized = _normalize_col_name(header_text)
    for col_key, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                return col_key
    return None


def parse_project_table(sb):
    """
    解析项目页的表格，返回:
    [
      {
        'row': <tr WebElement>,
        'model': 'free',
        'type': 'Discord bot',
        'service_id': '1a2a94d6-...',
        'expiry': '09/29/2026',
        'renewal': 'One-off',
        'status': 'Active',
      },
      ...
    ]
    """
    results = []
    try:
        tables = sb.driver.find_elements(By.TAG_NAME, 'table')
    except Exception:
        tables = []

    for table in tables:
        try:
            rows = table.find_elements(By.XPATH, './/tr')
        except Exception:
            continue
        if len(rows) < 2:
            continue

        # ---- 解析表头 ----
        header_map = {}  # col_index -> col_key
        data_rows = []
        for row in rows:
            try:
                ths = row.find_elements(By.TAG_NAME, 'th')
                if ths and not header_map:
                    for i, th in enumerate(ths):
                        key = _match_column(element_text(th))
                        if key:
                            header_map[i] = key
                    continue
                tds = row.find_elements(By.TAG_NAME, 'td')
                if tds:
                    data_rows.append((row, tds))
            except Exception:
                continue

        if not header_map:
            print("⚠️ 表格未识别到表头，跳过")
            continue

        # ---- 解析数据行 ----
        for row, tds in data_rows:
            item = {
                'row': row,
                'model': '',
                'type': '',
                'service_id': '',
                'expiry': '',
                'renewal': '',
                'status': '',
            }
            for i, td in enumerate(tds):
                key = header_map.get(i)
                if not key:
                    continue
                item[key] = element_text(td)
            # 跳过空行 / 表头重复行
            if item['service_id'] or item['expiry']:
                results.append(item)

    return results


def get_project_name_from_row(item):
    """从表格行里拼出一个人类可读的项目名。"""
    model = (item.get('model') or '').strip()
    type_ = (item.get('type') or '').strip()
    if model and type_:
        return f"{model} / {type_}"
    return model or type_ or (item.get('service_id') or '未知项目')


def get_service_uuid(item):
    """从 SERVICE ID 列取 uuid。"""
    return (item.get('service_id') or '').strip()


def get_project_expiry(item):
    """从 EXPIRY 列取过期时间。"""
    return (item.get('expiry') or '').strip() or '未知'


def get_renewal_available_note(item):
    """从 RENEWAL 列判断是否可续期。"""
    renewal = (item.get('renewal') or '').strip()
    if not renewal:
        return ''
    lowered = renewal.lower()
    if any(kw in lowered for kw in ('one-off', 'one off', 'unique', '一次性')):
        return '未到续期时间（One-off，需等到期前窗口）'
    if any(kw in lowered for kw in ('auto', 'automatic', 'automatique', '自动')):
        return '自动续期'
    if any(kw in lowered for kw in ('renew', 'renouvel')):
        return '当前可续期'
    return renewal


def find_renew_buttons_in_row(row):
    """在表格行里找续期按钮。"""
    selectors = [
        './/button['
        f'contains(translate(@title, "{_UPPER}", "{_LOWER}"), "renew") or '
        f'contains(translate(@title, "{_UPPER}", "{_LOWER}"), "renouvel") or '
        f'contains(translate(@aria-label, "{_UPPER}", "{_LOWER}"), "renew") or '
        f'contains(translate(@aria-label, "{_UPPER}", "{_LOWER}"), "renouvel")]',
        f'.//button[contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renew")]',
        f'.//button[contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renouvel")]',
        f'.//*[(@role="button" or self::a) and contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renew")]',
        f'.//*[(@role="button" or self::a) and contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renouvel")]',
    ]
    buttons = []
    for selector in selectors:
        try:
            buttons.extend(row.find_elements(By.XPATH, selector))
        except Exception:
            continue
    return unique_elements([
        b for b in buttons
        if element_text(b) or b.is_displayed()
    ])


def find_details_buttons_in_row(row):
    """在表格行里找 Details / Détails / 详情 按钮。"""
    selectors = [
        './/*[starts-with(@aria-label, "Details")]',
        './/*[starts-with(@aria-label, "Détails")]',
        './/*[starts-with(@aria-label, "详情")]',
    ]
    buttons = []
    for selector in selectors:
        try:
            buttons.extend(row.find_elements(By.XPATH, selector))
        except Exception:
            continue
    return unique_elements(buttons)


# ==================== 文本提取（保留，用于详情页兜底） ====================

def extract_date_like(text):
    if not text:
        return ''
    patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''


def extract_duration_like(text):
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if re.search(r'expires\s+in|expire\s+dans|expire\s+le|剩余|还有', line, re.I) and idx + 1 < len(lines):
            candidate = lines[idx + 1]
            if extract_date_like(candidate) or re.search(r'\d', candidate):
                return re.sub(
                    r'^(?:expires\s*in|expire\s*dans|expire\s*le|剩余|还有)\s*[:：]?\s*',
                    '', candidate, flags=re.I
                ).strip()
    match = re.search(
        r'(\d+\s*(?:jours?|j|heures?|h|days?|d|hours?|天|日|小时)\b)'
        r'(?:\s*\d+\s*(?:jours?|j|heures?|h|days?|d|hours?|天|日|小时)\b)?',
        text, re.I,
    )
    if match:
        return match.group(0).strip()
    return ''


# ==================== 诊断 & 验证码 ====================

def log_projects_page_diagnostics(sb):
    current_url = sb.get_current_url()
    title = sb.get_title()
    body_text = ''
    try:
        body_text = sb.driver.find_element(By.TAG_NAME, 'body').text.strip()
    except Exception:
        pass
    print(f"项目页诊断 URL: {current_url}")
    print(f"项目页诊断标题: {title}")
    print(f"项目页可见文本摘要: {body_text[:1200]}")


def click_captcha_checkbox(sb, label='验证码', timeout=10):
    selectors = [
        'div.auth-captcha-inner[role="checkbox"]',
        '//div[contains(., "Anti-bot confirmation")]//*[@role="checkbox"]',
        '//div[contains(., "I am not a robot")]//*[@role="checkbox"]',
        '//div[contains(@class, "modal") and contains(., "Secured by ACLClouds")]//*[@role="checkbox"]',
    ]
    last_error = None
    clicked = False
    selector = None
    for candidate in selectors:
        try:
            sb.wait_for_element_visible(candidate, timeout=timeout)
            scroll_to_selector(sb, candidate)
            sb.uc_click(candidate)
            sb.sleep(1)
            selector = candidate
            clicked = True
            break
        except Exception as e:
            last_error = e
            continue

    if not clicked:
        print(f"{label} 点击复选框失败: {last_error}")
        return False

    sb.sleep(5)
    captcha_ok = handle_captcha_challenge(sb, label, timeout=20)
    if not captcha_ok:
        print(f"{label} 验证流程未完成，等待状态仍未确认。")
        return False

    try:
        checked = sb.get_attribute(selector, 'aria-checked')
        if checked == 'true':
            print(f"{label} 验证通过")
            return True
        else:
            print(f"{label} 验证未完成，当前状态: {checked}")
            return False
    except Exception:
        return False


def handle_captcha_challenge(sb, label='验证码', timeout=20):
    start_time = time.time()
    challenge = None
    challenge_selectors = [
        '.auth-captcha-challenge',
        '.auth-capcha-challenge',
        '//*[contains(@class, "captcha") and contains(@class, "challenge")]',
        '//*[contains(@aria-label, "Click on ") or contains(@aria-label, "Select ") or contains(@class, "challenge")]',
    ]

    def get_challenge():
        for selector in challenge_selectors:
            try:
                if selector.startswith('/'):
                    elems = sb.driver.find_elements(By.XPATH, selector)
                    for elem in elems:
                        if elem.is_displayed():
                            return elem
                else:
                    elem = sb.wait_for_element_visible(selector, timeout=1)
                    if elem and elem.is_displayed():
                        return elem
            except Exception:
                continue
        return None

    while time.time() - start_time < timeout:
        challenge = get_challenge()
        if challenge:
            print(f"{label} 检测到图形验证码挑战")
            break
        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass
        sb.sleep(0.3)

    if not challenge:
        print(f"{label} 等待验证码挑战加载超时")
        return False

    target = ''
    try:
        prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
        target = prompt.text.strip()
    except Exception:
        pass
    if not target:
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-capcha-prompt strong')
            target = prompt.text.strip()
        except Exception:
            pass
    if not target:
        aria_label = challenge.get_attribute('aria-label') or ''
        if 'Click on ' in aria_label:
            target = aria_label.split('Click on ')[-1].strip()

    print(f"{label} 目标文本: {target or '未识别'}")

    option_selectors = [
        '.auth-captcha-option',
        '.auth-capcha-option',
        './/button',
        './/a',
        './/div[@role="button"]',
    ]

    def get_options(challenge_elem):
        for sel in option_selectors:
            try:
                if sel.startswith('.') or sel.startswith('['):
                    elems = challenge_elem.find_elements(By.CSS_SELECTOR, sel)
                else:
                    elems = challenge_elem.find_elements(By.XPATH, sel)
                if elems:
                    return [elem for elem in elems if elem.is_displayed() and elem.is_enabled()]
            except Exception:
                continue
        return []

    attempts = 0
    max_attempts = 8
    while attempts < max_attempts:
        challenge = get_challenge()
        if not challenge:
            return False
        options = get_options(challenge)
        if not options:
            attempts += 1
            sb.sleep(0.8)
            continue

        current_target = ''
        try:
            prompt = challenge.find_element(By.CSS_SELECTOR, '.auth-captcha-prompt strong')
            current_target = prompt.text.strip()
        except Exception:
            pass
        if not current_target:
            aria_label = challenge.get_attribute('aria-label') or ''
            if 'Click on ' in aria_label:
                current_target = aria_label.split('Click on ')[-1].strip()

        candidate = None
        effective_target = current_target or target
        if effective_target:
            for opt in options:
                opt_text = (opt.text or '').strip()
                if not opt_text:
                    try:
                        img = opt.find_element(By.TAG_NAME, 'img')
                        opt_text = (img.get_attribute('alt') or '').strip()
                    except Exception:
                        pass
                if not opt_text:
                    try:
                        opt_text = (opt.get_attribute('aria-label') or '').strip()
                    except Exception:
                        pass
                if opt_text and effective_target.lower() in opt_text.lower():
                    candidate = opt
                    break
        if candidate is None:
            candidate = options[0]

        print(f"{label} 点击候选选项 #{attempts + 1} ...")
        clicked = safe_click_element(sb, candidate, f"{label} 选项候选")
        if not clicked:
            attempts += 1
            sb.sleep(0.8)
            continue
        sb.sleep(4.5)

        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选，验证码流程已完成")
                return True
        except Exception:
            pass
        if not get_challenge():
            print(f"{label} 挑战已消失，验证完成")
            return True
        attempts += 1

    print(f"{label} 多次尝试后仍未完成验证码")
    return False


# ==================== 通知消息 ====================

def mask_email(email):
    if not email or '@' not in email:
        return email or ''
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        masked_local = local[0] + '****' if local else '****'
    elif len(local) <= 4:
        masked_local = f"{local[0]}****{local[-1]}"
    else:
        masked_local = f"{local[:2]}****{local[-2:]}"
    return f"{masked_local}@{domain}"


def build_success_message(project_name, old_expiry, new_expiry):
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "✅ 续期成功",
        f"📦 项目: {project_name}",
        f"⏱️ 旧过期: {old_expiry}",
        f"⏱️ 新过期: {new_expiry}",
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ]
    return "\n".join(lines)


def build_not_yet_due_message(project_name, expiry, note=''):
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "⏳ 未到续期时间",
        f"📦 项目: {project_name}",
        f"⏱️ 当前过期时间: {expiry}",
    ]
    if note:
        lines.append(f"📅 续期状态: {note}")
    lines.extend([
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ])
    return "\n".join(lines)


def build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note):
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        f"❌ 续期状态未确认: {project_name}",
        f"👤 登录账户: {mask_email(EMAIL)}",
    ]
    if old_expiry:
        lines.append(f"旧过期: {old_expiry}")
    lines.extend([
        f"当前过期: {new_expiry}",
        f"页面提示: {result_note or '未发现成功提示'}",
    ])
    return "\n".join(lines)


# ==================== 续期人机验证 ====================

def handle_renew_antibot(sb, project_name):
    modal_selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]
    for selector in modal_selectors:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=5)
        except Exception:
            continue
    print(f"[{project_name}] 未检测到续期人机验证窗口，继续等待续期结果")
    return False


# ==================== 登录 ====================

def js_set_input_value(sb, selector, value):
    sb.execute_script(
        '''
        const el = document.querySelector(arguments[0]);
        if (!el) return false;
        el.focus();
        el.value = arguments[1];
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        return true;
        ''',
        selector,
        value,
    )


def fill_input(sb, selector, value, label, timeout=15):
    sb.wait_for_element_visible(selector, timeout=timeout)
    scroll_to_selector(sb, selector)
    sb.click(selector)
    sb.clear(selector)
    sb.type(selector, value)
    entered_value = sb.get_value(selector)
    if label == '密码':
        print(f"{label}输入框当前值长度: {len(entered_value)}")
    else:
        print(f"{label}输入框当前值: '{entered_value}'")
    if entered_value != value:
        print(f"{label}输入未生效，使用 JavaScript 强制赋值并触发事件")
        js_set_input_value(sb, selector, value)
        entered_value = sb.get_value(selector)
    return entered_value == value


def login(sb, email, password):
    print("开始登录流程...")
    fill_input(sb, '#username', email, '邮箱')
    fill_input(sb, '#password', password, '密码')

    captcha_ok = click_captcha_checkbox(sb, '登录验证码')
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮")
        return False

    sb.sleep(1)
    login_page_url = sb.get_current_url()
    clicked = False
    for selector in ['button[type="submit"]', 'div.auth-submit-btn',
                     '//button[contains(text(), "Sign in")]',
                     '//div[contains(text(), "Sign in")]']:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            scroll_to_selector(sb, selector)
            sb.click(selector)
            clicked = True
            print(f"点击 Sign in 使用: {selector}")
            break
        except Exception as e:
            print(f"选择器 {selector} 失败: {e}")
    if not clicked:
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim() === 'Sign in') { el.click(); return true; }
            }
            return false;
        ''')

    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        current = sb.get_current_url()
        if '/auth/login' not in current and '/dashboard' in current:
            print(f"✅ 登录成功！URL: {current}")
            return True
        else:
            print(f"❌ 登录失败，当前: {current}")
            return False
    except Exception as e:
        print(f"登录过程异常: {e}")
        return False


# ==================== 代理 / IP ====================

def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        normalized = proxy_server.replace("socks://", "socks5h://")
        proxies = {"http": normalized, "https": normalized}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


# ==================== 主流程 ====================

def main():
    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.getenv('S5_PROXY') or os.getenv('PROXY_SERVER') or "socks5://127.0.0.1:1080"
    if PROXY_SERVER.startswith("socks://"):
        PROXY_SERVER = PROXY_SERVER.replace("socks://", "socks5h://", 1)

    HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"
    sb_options = {'uc': True, 'headless': HEADLESS}
    if IS_PROXY:
        sb_options['proxy'] = PROXY_SERVER
        print(f"🔗 挂载代理: {PROXY_SERVER}")
    else:
        print("🍭 未使用代理，直连访问")

    with SB(**sb_options) as sb:
        try:
            ip = get_current_ip(PROXY_SERVER if IS_PROXY else "")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"获取出口IP失败: {e}")

        sb.set_window_size(1366, 768)

        print(f"打开项目页: {PROJECTS_URL}")
        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(2)

        if not is_logged_in(sb):
            print(f"未登录（当前: {sb.get_current_url()}），前往登录页...")
            sb.open(f"{BASE_URL}{LOGIN_PATH}")
            sb.wait_for_ready_state_complete()
            time.sleep(2)
            if not is_login_page(sb):
                print(f"❌ 无法打开登录页")
                send_telegram(f"⚠️ 无法打开登录页")
                return
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 EMAIL 或 PASSWORD")
                send_telegram("⚠️ 未配置 EMAIL 或 PASSWORD。")
                return
            if not login(sb, EMAIL, PASSWORD):
                return
            sb.open(PROJECTS_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(3)
        else:
            print(f"✅ 当前已登录: {sb.get_current_url()}")

        if '/dashboard/projects' not in sb.get_current_url():
            sb.open(PROJECTS_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(3)

        print("等待项目表格渲染...")
        wait_for_spa_ready(sb, timeout=20)
        sb.sleep(1)

        # ★ 核心：解析表格
        items = parse_project_table(sb)

        if not items:
            print("❌ 未解析到任何项目行。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未解析到项目行，请检查表格结构。")
            return

        print(f"找到 {len(items)} 个项目行。")

        # ---- 打印每行信息 ----
        for idx, item in enumerate(items, 1):
            name = get_project_name_from_row(item)
            uuid = get_service_uuid(item)
            expiry = get_project_expiry(item)
            renewal = get_renewal_available_note(item)
            print(f"  [{idx}] {name}  id={uuid[:8] or '-'}  expiry={expiry}  renewal={renewal}")

        # ---- 处理每一行 ----
        for idx, item in enumerate(items, 1):
            project_name = get_project_name_from_row(item)
            service_id = get_service_uuid(item)
            old_expiry = get_project_expiry(item)
            renewal_note = get_renewal_available_note(item)

            try:
                row = item.get('row')
                if row is None:
                    continue

                renew_btns = find_renew_buttons_in_row(row)
                if renew_btns:
                    # 有续期按钮 → 点击续期
                    print(f"[{project_name}] 检测到续期按钮，点击续期...")
                    action_label = 'Renew'
                    safe_click_element(sb, renew_btns[0], f"[{project_name}] {action_label}按钮")
                    handle_renew_antibot(sb, project_name)
                    sb.sleep(3)
                    # 重新解析表格，读取新过期时间
                    items_after = parse_project_table(sb)
                    new_expiry = old_expiry
                    for it in items_after:
                        if get_service_uuid(it) == service_id:
                            new_expiry = get_project_expiry(it)
                            break
                    if new_expiry != old_expiry:
                        print(f"[{project_name}] 续期成功！旧: {old_expiry} → 新: {new_expiry}")
                        send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    else:
                        print(f"[{project_name}] 续期状态未确认，过期时间未变: {new_expiry}")
                        send_telegram(build_unconfirmed_message(project_name, old_expiry, new_expiry, '按钮已点击但过期未变'))
                else:
                    # 无续期按钮 → 未到续期时间
                    print(f"[{project_name}] 无续期按钮 → 未到续期时间（{renewal_note or '未知'}）")
                    send_telegram(build_not_yet_due_message(project_name, old_expiry, renewal_note))

            except Exception as e:
                print(f"处理项目 {project_name} 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

        print("所有项目处理完成。")


if __name__ == '__main__':
    main()
