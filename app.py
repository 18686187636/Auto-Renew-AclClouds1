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

SUCCESS_KEYWORDS = ('successfully', 'avec succès', 'réussi', 'succès', '成功')

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ"
_LOWER = "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"

UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.I,
)

DETAILS_LABEL_RE = re.compile(
    r'^(?:Details|Détails|详情)\s*[:：]\s*(.+)$',
    re.I,
)


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
            print(f"Telegram sent:\n{message}")
        except Exception as e:
            print(f"Failed to send Telegram: {e}")
    else:
        print(f"[Telegram disabled] {message}")


def wait_for_url_change(sb, original_url, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        if sb.get_current_url() != original_url:
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
        print(f"{label} 元素已失效")
        return False


# ==================== SPA 等待 ====================

def wait_for_spa_ready(sb, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        try:
            found = sb.driver.execute_script('''
                const re = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
                return re.test(document.body.innerText || '');
            ''')
            if found:
                return True
        except Exception:
            pass
        sb.sleep(0.5)
    print("⚠️ 等待 SPA 渲染超时")
    return False


# ==================== 验证码处理（核心修复） ====================

def _get_challenge(sb, challenge_selectors):
    for selector in challenge_selectors:
        try:
            if selector.startswith('/'):
                for elem in sb.driver.find_elements(By.XPATH, selector):
                    if elem.is_displayed():
                        return elem
            else:
                elem = sb.wait_for_element_visible(selector, timeout=1)
                if elem and elem.is_displayed():
                    return elem
        except Exception:
            continue
    return None


def _get_captcha_target(ch):
    """从挑战容器里提取目标文本。"""
    # 1) prompt strong
    for sel in ('.auth-captcha-prompt strong', '.auth-capcha-prompt strong'):
        try:
            t = ch.find_element(By.CSS_SELECTOR, sel).text.strip()
            if t:
                return t
        except Exception:
            pass
    # 2) aria-label
    try:
        aria_label = ch.get_attribute('aria-label') or ''
        m = re.search(r'(?:Click on|Select)\s+(.+)', aria_label, re.I)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    # 3) 整个挑战文本
    try:
        text = (ch.text or '').strip()
        m = re.search(r'(?:Click on|Select)\s+["\']?([^"\'\n]+)', text, re.I)
        if m:
            return m.group(1).strip()
    except Exception:
        pass
    return ''


def _get_captcha_options(ch):
    for sel in (
        '.auth-captcha-option',
        '.auth-capcha-option',
        './/button',
        './/a',
        './/div[@role="button"]',
    ):
        try:
            if sel.startswith('.') or sel.startswith('['):
                elems = ch.find_elements(By.CSS_SELECTOR, sel)
            else:
                elems = ch.find_elements(By.XPATH, sel)
            visible = [e for e in elems if e.is_displayed() and e.is_enabled()]
            if visible:
                return visible
        except Exception:
            continue
    return []


def _get_option_text(opt):
    """收集一个选项的所有可能文本信息，用于匹配目标。"""
    parts = []
    try:
        t = (opt.text or '').strip()
        if t:
            parts.append(t)
    except Exception:
        pass
    for attr in ('aria-label', 'title', 'data-value', 'value', 'name'):
        try:
            t = (opt.get_attribute(attr) or '').strip()
            if t:
                parts.append(t)
        except Exception:
            pass
    try:
        for img in opt.find_elements(By.TAG_NAME, 'img'):
            for attr in ('alt', 'title', 'data-name'):
                t = (img.get_attribute(attr) or '').strip()
                if t:
                    parts.append(t)
            src = (img.get_attribute('src') or '').strip()
            if src:
                fname = src.split('/')[-1].split('?')[0]
                name = re.sub(r'\.(png|jpg|jpeg|webp|gif|svg)$', '', fname, flags=re.I)
                if name:
                    parts.append(name)
    except Exception:
        pass
    return ' '.join(parts).lower()


def handle_captcha_challenge(sb, label='验证码', timeout=20):
    start_time = time.time()
    challenge_selectors = [
        '.auth-captcha-challenge',
        '.auth-capcha-challenge',
        '//*[contains(@class, "captcha") and contains(@class, "challenge")]',
        '//*[contains(@aria-label, "Click on ") or contains(@aria-label, "Select ") or contains(@class, "challenge")]',
    ]

    challenge = None
    while time.time() - start_time < timeout:
        challenge = _get_challenge(sb, challenge_selectors)
        if challenge:
            print(f"{label} 检测到图形验证码挑战")
            break
        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选")
                return True
        except Exception:
            pass
        sb.sleep(0.3)

    if not challenge:
        print(f"{label} 等待验证码挑战加载超时")
        return False

    target = _get_captcha_target(challenge)
    print(f"{label} 目标文本: {target or '未识别'}")

    # 记录上一次的选项数量和已尝试过的索引
    tried_indices = set()
    last_option_count = 0
    attempts = 0
    max_attempts = 10

    while attempts < max_attempts:
        challenge = _get_challenge(sb, challenge_selectors)
        if not challenge:
            print(f"{label} 挑战已消失，验证完成")
            return True

        options = _get_captcha_options(challenge)
        if not options:
            attempts += 1
            sb.sleep(0.8)
            continue

        # 选项数量变化 → 说明刷新了，重置尝试记录
        if len(options) != last_option_count:
            tried_indices.clear()
            last_option_count = len(options)

        # 每次重新读目标（可能刷新）
        current_target = _get_captcha_target(challenge) or target

        # 策略 1：文本匹配
        matched_idx = None
        if current_target:
            target_lower = current_target.lower()
            for i, opt in enumerate(options):
                opt_text = _get_option_text(opt)
                if opt_text and target_lower in opt_text:
                    matched_idx = i
                    print(f"{label} 文本匹配到选项 #{i + 1}: '{opt_text[:60]}'")
                    break

        # 策略 2：轮询未尝试过的选项
        if matched_idx is None:
            for i in range(len(options)):
                if i not in tried_indices:
                    matched_idx = i
                    print(f"{label} 无匹配，轮询选项 #{i + 1}")
                    break
            if matched_idx is None:
                tried_indices.clear()
                matched_idx = 0
                print(f"{label} 全部尝试过，重置从 #1 开始")

        candidate = options[matched_idx]
        tried_indices.add(matched_idx)

        print(f"{label} 点击候选选项 #{attempts + 1} (index={matched_idx}) ...")
        if not safe_click_element(sb, candidate, f"{label} 选项"):
            attempts += 1
            sb.sleep(0.8)
            continue
        sb.sleep(4)

        try:
            checkbox = sb.driver.find_element(By.CSS_SELECTOR, 'div.auth-captcha-inner[role="checkbox"]')
            if checkbox.get_attribute('aria-checked') == 'true':
                print(f"{label} 验证复选框已勾选")
                return True
        except Exception:
            pass

        if not _get_challenge(sb, challenge_selectors):
            print(f"{label} 挑战已消失，验证完成")
            return True

        attempts += 1

    print(f"{label} 多次尝试后仍未完成验证码")
    return False


def click_captcha_checkbox(sb, label='验证码', timeout=10):
    selectors = [
        'div.auth-captcha-inner[role="checkbox"]',
        '//div[contains(., "Anti-bot confirmation")]//*[@role="checkbox"]',
        '//div[contains(., "I am not a robot")]//*[@role="checkbox"]',
    ]
    last_error = None
    selector = None
    clicked = False
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
    if not clicked:
        print(f"{label} 点击复选框失败: {last_error}")
        return False

    sb.sleep(5)
    if not handle_captcha_challenge(sb, label, timeout=20):
        print(f"{label} 验证流程未完成")
        return False

    try:
        checked = sb.get_attribute(selector, 'aria-checked')
        if checked == 'true':
            print(f"{label} 验证通过")
            return True
        print(f"{label} 验证未完成，状态: {checked}")
        return False
    except Exception:
        return False


# ==================== div-based 表格解析 ====================

def parse_project_table(sb):
    js_script = '''
    const UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

    const detailNameMap = {};

    document.querySelectorAll('[aria-controls^="service-details-"]').forEach(btn => {
        const controls = btn.getAttribute('aria-controls') || '';
        const uuid = controls.replace('service-details-', '').toLowerCase();
        if (!uuid) return;
        const label = btn.getAttribute('aria-label') || '';
        const m = label.match(/^(?:Details|Détails|详情)\\s*[:：]\\s*(.+)$/i);
        if (m) detailNameMap[uuid] = m[1].trim();
    });

    document.querySelectorAll(
        '[aria-label^="Details"], [aria-label^="Détails"], [aria-label^="详情"]'
    ).forEach(btn => {
        const label = btn.getAttribute('aria-label') || '';
        const m = label.match(/^(?:Details|Détails|详情)\\s*[:：]\\s*(.+)$/i);
        if (!m) return;
        const name = m[1].trim();
        let cur = btn;
        for (let i = 0; i < 12 && cur; i++, cur = cur.parentElement) {
            const txt = (cur.innerText || '');
            const um = txt.match(UUID_RE);
            if (um) {
                detailNameMap[um[0].toLowerCase()] = name;
                break;
            }
        }
    });

    document.querySelectorAll('h3, h4').forEach(h => {
        const name = (h.textContent || '').trim();
        if (!name) return;
        let cur = h;
        for (let i = 0; i < 12 && cur; i++, cur = cur.parentElement) {
            const txt = (cur.innerText || '');
            const um = txt.match(UUID_RE);
            if (um) {
                const uuid = um[0].toLowerCase();
                if (!detailNameMap[uuid]) detailNameMap[uuid] = name;
                break;
            }
        }
    });

    const all = document.querySelectorAll('*');
    const uuidNodes = [];
    for (const el of all) {
        if (el.children.length === 0) {
            const t = (el.textContent || '').trim();
            if (UUID_RE.test(t)) uuidNodes.push(el);
        }
    }

    function findRowContainer(node) {
        let cur = node;
        for (let i = 0; i < 12 && cur; i++, cur = cur.parentElement) {
            if (!cur.parentElement) break;
            const text = (cur.innerText || '').trim();
            if (text.length > 600) continue;
            if (/\\d/.test(text) && text.split('\\n').filter(s => s.trim()).length >= 3) {
                return cur;
            }
        }
        return null;
    }

    const seen = new Set();
    const rows = [];
    for (const n of uuidNodes) {
        const row = findRowContainer(n);
        if (!row) continue;
        const uuidMatch = (row.innerText || '').match(UUID_RE);
        if (!uuidMatch) continue;
        const uuid = uuidMatch[0].toLowerCase();
        if (seen.has(uuid)) continue;
        seen.add(uuid);

        let inlineDetailsLabel = '';
        try {
            const detailBtn = row.querySelector(
                '[aria-controls^="service-details-"], ' +
                '[aria-label^="Details"], ' +
                '[aria-label^="Détails"], ' +
                '[aria-label^="详情"]'
            );
            if (detailBtn) {
                inlineDetailsLabel = detailBtn.getAttribute('aria-label') || '';
            }
        } catch (e) {}

        const texts = [];
        const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT, null);
        let t;
        while (t = walker.nextNode()) {
            const s = (t.textContent || '').trim();
            if (s) texts.push(s);
        }

        const mappedName = detailNameMap[uuid] || '';
        const inlineName = inlineDetailsLabel
            ? (inlineDetailsLabel.match(/^(?:Details|Détails|详情)\\s*[:：]\\s*(.+)$/i) || [])[1] || ''
            : '';

        rows.push({
            uuid: uuid,
            texts: texts,
            detailsLabel: mappedName || inlineName || ''
        });
    }
    return rows;
    '''

    try:
        raw_rows = sb.driver.execute_script(js_script)
    except Exception as e:
        print(f"⚠️ 执行 JS 解析失败: {e}")
        return []

    if not raw_rows:
        return []

    results = []
    for raw in raw_rows:
        texts = raw.get('texts', [])
        uuid = raw.get('uuid', '').lower()
        details_label = raw.get('detailsLabel', '') or ''
        if not texts or not uuid:
            continue

        uuid_idx = -1
        for i, t in enumerate(texts):
            if UUID_RE.search(t):
                uuid_idx = i
                break
        if uuid_idx < 0:
            continue

        before = texts[:uuid_idx]
        after = texts[uuid_idx + 1:]

        model = before[0] if len(before) > 0 else ''
        type_ = before[1] if len(before) > 1 else ''

        expiry = ''
        renewal = ''
        status = ''
        for t in after:
            if not expiry and re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', t):
                expiry = t
            elif not renewal and re.search(r'one[\s-]?off|auto|renew|renouvel|unique', t, re.I):
                renewal = t
            elif not status and re.search(r'active|pending|suspend|cancel|expir', t, re.I):
                status = t

        results.append({
            'uuid': uuid,
            'model': model,
            'type': type_,
            'service_id': uuid,
            'expiry': expiry or '未知',
            'renewal': renewal or '',
            'status': status or '',
            'details_label': details_label,
        })

    return results


def extract_name_from_details_label(label):
    if not label:
        return ''
    m = DETAILS_LABEL_RE.match(label.strip())
    if m:
        return m.group(1).strip()
    return ''


def get_project_name_from_item(item):
    name = extract_name_from_details_label(item.get('details_label', ''))
    if name:
        return name

    model = (item.get('model') or '').strip()
    type_ = (item.get('type') or '').strip()
    if model and type_:
        return f"{model} / {type_}"
    return model or type_ or (item.get('service_id') or '未知项目')


def get_renewal_available_note(item):
    renewal = (item.get('renewal') or '').strip()
    if not renewal:
        return ''
    lowered = renewal.lower()
    if any(kw in lowered for kw in ('one-off', 'one off', 'unique', '一次性')):
        return '未到续期时间（One-off）'
    if any(kw in lowered for kw in ('auto', 'automatic', 'automatique', '自动')):
        return '自动续期'
    if any(kw in lowered for kw in ('renew', 'renouvel')):
        return '当前可续期'
    return renewal


def find_renew_button_for_uuid(sb, uuid):
    xpath = (
        f'//*[contains(text(), "{uuid}")]'
        f'/ancestor::*[position()<=8]'
        f'//button['
        f'contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renew") or '
        f'contains(translate(normalize-space(.), "{_UPPER}", "{_LOWER}"), "renouvel")'
        f']'
    )
    try:
        btns = sb.driver.find_elements(By.XPATH, xpath)
        return [b for b in btns if b.is_displayed()]
    except Exception:
        return []


# ==================== 诊断 ====================

def log_projects_page_diagnostics(sb):
    try:
        body_text = sb.driver.find_element(By.TAG_NAME, 'body').text.strip()
    except Exception:
        body_text = ''
    print(f"项目页诊断 URL: {sb.get_current_url()}")
    print(f"项目页诊断标题: {sb.get_title()}")
    print(f"项目页可见文本摘要: {body_text[:1200]}")


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
    return "\n".join([
        "🇫🇷 Aclclouds 续期通知",
        "",
        "✅ 续期成功",
        f"📦 项目: {project_name}",
        f"⏱️ 旧过期: {old_expiry}",
        f"⏱️ 新过期: {new_expiry}",
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ])


def build_not_yet_due_message(project_name, expiry, note=''):
    return "\n".join([
        "🇫🇷 Aclclouds 续期通知",
        "",
        "⏳ 未到续期时间",
        f"📦 项目: {project_name}",
        f"⏱️ 当前过期时间: {expiry}",
        f"👤 登录账户: {mask_email(EMAIL)}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ])


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
    for selector in [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]:
        try:
            sb.wait_for_element_visible(selector, timeout=5)
            print(f"[{project_name}] 检测到续期人机验证窗口")
            return click_captcha_checkbox(sb, '续期人机验证', timeout=5)
        except Exception:
            continue
    print(f"[{project_name}] 未检测到续期人机验证窗口")
    return False


# ==================== 登录（带重试） ====================

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
        selector, value,
    )


def fill_input(sb, selector, value, label, timeout=15):
    sb.wait_for_element_visible(selector, timeout=timeout)
    scroll_to_selector(sb, selector)
    sb.click(selector)
    sb.clear(selector)
    sb.type(selector, value)
    entered = sb.get_value(selector)
    print(f"{label}输入框当前值: '{entered}'" if label != '密码' else f"{label}输入框当前值长度: {len(entered)}")
    if entered != value:
        print(f"{label}输入未生效，使用 JS 强制赋值")
        js_set_input_value(sb, selector, value)
        entered = sb.get_value(selector)
    return entered == value


def login(sb, email, password, max_retries=3):
    for retry in range(max_retries):
        if retry > 0:
            print(f"🔄 登录重试 #{retry + 1}/{max_retries}")
            sb.open(f"{BASE_URL}{LOGIN_PATH}")
            sb.wait_for_ready_state_complete()
            sb.sleep(2)

        print(f"开始登录流程（尝试 {retry + 1}/{max_retries}）...")
        fill_input(sb, '#username', email, '邮箱')
        fill_input(sb, '#password', password, '密码')

        if not click_captcha_checkbox(sb, '登录验证码'):
            print("⚠️ 登录验证码未完成，准备重试")
            continue

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
            print(f"❌ 登录失败，当前: {current}")
        except Exception as e:
            print(f"登录过程异常: {e}")

    print(f"❌ {max_retries} 次尝试后登录仍失败")
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
            print("未登录，前往登录页...")
            sb.open(f"{BASE_URL}{LOGIN_PATH}")
            sb.wait_for_ready_state_complete()
            time.sleep(2)
            if not is_login_page(sb):
                print("❌ 无法打开登录页")
                send_telegram("⚠️ 无法打开登录页")
                return
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 EMAIL 或 PASSWORD")
                send_telegram("⚠️ 未配置 EMAIL 或 PASSWORD。")
                return
            if not login(sb, EMAIL, PASSWORD, max_retries=3):
                send_telegram("⚠️ 登录失败（已重试 3 次），请检查账号或验证码。")
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

        items = parse_project_table(sb)
        if not items:
            print("❌ 未解析到任何项目行。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未解析到项目行，请检查表格结构。")
            return

        print(f"找到 {len(items)} 个项目行。")
        for idx, item in enumerate(items, 1):
            name = get_project_name_from_item(item)
            print(
                f"  [{idx}] {name}  "
                f"id={item['service_id'][:8]}  "
                f"expiry={item['expiry']}  "
                f"renewal={item['renewal'] or '-'}  "
                f"status={item['status'] or '-'}  "
                f"details_label='{item.get('details_label', '')}'"
            )

        for idx, item in enumerate(items, 1):
            project_name = get_project_name_from_item(item)
            service_id = item['service_id']
            old_expiry = item['expiry']
            renewal_note = get_renewal_available_note(item)

            try:
                renew_btns = find_renew_button_for_uuid(sb, service_id)
                if renew_btns:
                    print(f"[{project_name}] 检测到续期按钮，点击续期...")
                    safe_click_element(sb, renew_btns[0], f"[{project_name}] Renew")
                    handle_renew_antibot(sb, project_name)
                    sb.sleep(3)
                    items_after = parse_project_table(sb)
                    new_expiry = old_expiry
                    for it in items_after:
                        if it['service_id'] == service_id:
                            new_expiry = it['expiry']
                            break
                    if new_expiry != old_expiry:
                        print(f"[{project_name}] 续期成功！{old_expiry} → {new_expiry}")
                        send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    else:
                        print(f"[{project_name}] 续期状态未确认，过期未变: {new_expiry}")
                        send_telegram(build_unconfirmed_message(project_name, old_expiry, new_expiry, '按钮已点击但过期未变'))
                else:
                    print(f"[{project_name}] 无续期按钮 → 未到续期时间（{renewal_note or '未知'}）")
                    send_telegram(build_not_yet_due_message(project_name, old_expiry, renewal_note))
            except Exception as e:
                print(f"处理项目 {project_name} 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

        print("所有项目处理完成。")


if __name__ == '__main__':
    main()
