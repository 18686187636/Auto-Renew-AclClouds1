#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from seleniumbase import SB
from selenium.common.exceptions import ElementClickInterceptedException, WebDriverException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from zoneinfo import ZoneInfo

# ----- 配置（从环境变量读取或在双引号内填写） -----
EMAIL = os.getenv('EMAIL') or ""
PASSWORD = os.getenv('PASSWORD') or ""
TG_CHAT_ID = os.getenv('TG_CHAT_ID') or ""
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN') or ""

LOGIN_PATH = '/auth/login'
BASE_URL = 'https://dash.aclclouds.com'
PROJECTS_URL = f'{BASE_URL}/dashboard/projects'

# ★ 多语言关键词
EXPIRE_LABELS = ('Expire dans', 'Expires in', '到期', '过期')
RENEW_KEYWORDS = ('renew', 'renouveler', 'renouvellement', '续期')
REACTIVATE_KEYWORDS = ('reactivate', 'réactiver', 'reactiver', '重新激活', '恢复')
MANAGE_KEYWORDS = ('manage', 'gérer', 'gerer', '管理')
SUSPEND_KEYWORDS = ('suspended', 'suspendu', 'paused', 'en pause', '暂停')
SUCCESS_KEYWORDS = ('successfully', 'avec succès', 'réussi', 'succès', '成功')


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
    current_url = sb.get_current_url()
    return BASE_URL in current_url and LOGIN_PATH not in current_url


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


# ★ 修复：原实现用 list.count()，永远返回 False，导致父子卡片去重完全失效
def element_contains(parent, child):
    if parent == child:
        return True
    try:
        return child in parent.find_elements(By.XPATH, './/*')
    except Exception:
        return False


def dedupe_project_cards(cards):
    cards = unique_elements(cards)
    if not cards:
        return []

    keep = []
    for card in cards:
        card_text = element_text(card)
        if len(card_text) < 3:
            continue

        duplicate = False
        for kept in list(keep):
            kept_text = element_text(kept)
            if element_contains(kept, card):
                duplicate = True
                break
            if element_contains(card, kept):
                if len(card_text) > len(kept_text):
                    keep.remove(kept)
                else:
                    duplicate = True
                break

        if not duplicate:
            keep.append(card)

    deduped = []
    seen_signatures = set()
    for card in keep:
        text = element_text(card)
        name = ''
        for line in text.splitlines():
            line = line.strip()
            if line and not re.search(r'expire|expiry|renouvel|renew|réactiv|reactivat|suspend|pause|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
                name = line
                break
        signature = (name.lower(), get_project_expiry(card).lower())
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        deduped.append(card)

    return deduped


def find_elements(root, selector):
    # ★ 扩展：识别以 ( 开头的 XPath 和常见轴
    if selector.startswith(('/', './/', '(', 'ancestor', 'descendant', 'following', 'preceding')):
        by = By.XPATH
    else:
        by = By.CSS_SELECTOR
    return root.find_elements(by, selector)


def find_renew_buttons(root):
    selectors = [
        '.projects-renew-btn',
        # Renew / Renouveler（英/法）
        './/button['
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renew") or '
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renouvel") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renew") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renouvel")]',
        # Reactivate / Réactiver
        './/button['
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "reactivate") or '
        'contains(translate(@title, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "réactiv") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "reactivate") or '
        'contains(translate(@aria-label, "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "réactiv")]',
        # 文本匹配
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renew")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renouvel")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "reactivate")]',
        './/button[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "réactiv")]',
        # 通用 role=button / a
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renew")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "renouvel")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "reactivate")]',
        './/*[(@role="button" or self::a) and contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "réactiv")]',
    ]
    buttons = []
    for selector in selectors:
        try:
            buttons.extend(find_elements(root, selector))
        except Exception:
            continue
    return unique_elements([button for button in buttons if element_text(button) or button.is_displayed()])


# ★ 重写：从子节点向上爬，找到真正的卡片容器；找不到返回 None，绝不返回 body/main
def find_card_container_from_child(sb, child):
    return sb.driver.execute_script(
        '''
        const start = arguments[0];
        if (!start) return null;

        const stopTags = ['body', 'html', 'main', 'header', 'footer', 'nav', 'form'];
        const hasExpire = (t) => /expire dans|expires in|expir|到期|过期/i.test(t);
        const hasAction = (t) => /manage|gérer|gerer|renouveler|renew|réactiver|reactivate|supprimer|delete/i.test(t);

        let node = start;
        let fallback = null;

        for (let i = 0; node && i < 15; i += 1, node = node.parentElement) {
            if (node === start) continue;

            const tag = (node.tagName || '').toLowerCase();
            if (stopTags.includes(tag)) break;

            const text = (node.innerText || '').trim();
            if (text.length > 1000) break;

            // 尺寸合理 + 含过期标签 + 含操作按钮 → 基本就是卡片
            if (text.length >= 30 && text.length <= 600 && hasExpire(text) && hasAction(text)) {
                return node;
            }

            // 尺寸合理 + 含过期标签 + class 像卡片
            const cls = (node.className || '').toString().toLowerCase();
            if (text.length >= 30 && text.length <= 600 && hasExpire(text) &&
                /card|project|service|server|item|row|vps|bot/.test(cls)) {
                return node;
            }

            // 记下第一个满足最小条件的节点作为候选
            if (!fallback && text.length >= 30 && text.length <= 600 && hasExpire(text)) {
                fallback = node;
            }
        }

        return fallback;
        ''',
        child,
    )


# ★ 重写：以 "Expire dans" / "Expires in" 标签为锚点定位卡片
def find_project_cards(sb):
    cards = []

    # 方法 1：以过期标签为锚点（法语 "Expire dans" / 英语 "Expires in"）
    anchor_xpath = (
        '//*[self::div or self::span or self::p or self::label or self::td or self::th]'
        '[normalize-space(.) = "Expire dans" '
        'or normalize-space(.) = "Expires in" '
        'or normalize-space(.) = "到期" '
        'or normalize-space(.) = "过期"]'
    )
    try:
        anchors = sb.driver.find_elements(By.XPATH, anchor_xpath)
    except Exception:
        anchors = []

    for anchor in anchors:
        try:
            card = find_card_container_from_child(sb, anchor)
            if card is not None:
                cards.append(card)
        except Exception:
            continue

    # 方法 2：以续期/重新激活按钮为锚点（兜底）
    if not cards:
        try:
            for button in find_renew_buttons(sb.driver):
                try:
                    card = find_card_container_from_child(sb, button)
                    if card is not None:
                        cards.append(card)
                except Exception:
                    continue
        except Exception:
            pass

    return dedupe_project_cards(cards)


def extract_date_like(text):
    if not text:
        return ''
    patterns = [
        r'\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
        r'\d{1,2}[-/]\d{1,2}[-/]\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ''


# ★ 扩展：支持法语 "4j 14h" / "3 jours 2 heures" / 英语 / 中文
def extract_duration_like(text):
    if not text:
        return ''

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        if re.search(r'expires\s+in|expire\s+dans|剩余|还有', line, re.I) and idx + 1 < len(lines):
            candidate = lines[idx + 1]
            if extract_date_like(candidate) or re.search(r'\d', candidate):
                return re.sub(
                    r'^(?:expires\s*in|expire\s*dans|剩余|还有)\s*[:：]?\s*',
                    '', candidate, flags=re.I
                ).strip()

    # 法语: 4j 14h / 3 jours 2 heures；英语: 5 days 4 hours；中文: 4天14小时
    match = re.search(
        r'(\d+\s*(?:jours?|j|heures?|h|days?|d|hours?|天|日|小时)\b)'
        r'(?:\s*\d+\s*(?:jours?|j|heures?|h|days?|d|hours?|天|日|小时)\b)?',
        text, re.I,
    )
    if match:
        return match.group(0).strip()

    return ''


def get_project_name(card, idx):
    selectors = [
        '.projects-card-title',
        'h1', 'h2', 'h3', 'h4',
        '[class*="title"]',
        '[class*="name"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text and len(text) <= 80 \
                        and not re.search(r'renew|renouvel|expiry|expire', text, re.I) \
                        and not extract_duration_like(text):
                    return text
        except Exception:
            continue

    for line in element_text(card).splitlines():
        line = line.strip()
        if line and len(line) <= 80 \
                and not extract_duration_like(line) \
                and not re.search(r'renew|renouvel|réactiv|reactivat|suspended|suspendu|expiry|expire|expire dans|expires in|valid|manage|gérer|gerer|续期|重新激活|恢复|暂停|过期|到期', line, re.I):
            return line
    return f"项目 #{idx}"


# ★ 重写：先按 "Expire dans" / "Expires in" 精确匹配标签，取紧邻兄弟节点
def get_project_expiry(card):
    # 精确匹配标签文本，取紧随其后的兄弟节点
    for label in EXPIRE_LABELS:
        try:
            labels = card.find_elements(
                By.XPATH,
                f'.//*[normalize-space(.) = "{label}"]',
            )
            for lab in labels:
                try:
                    value_elem = lab.find_element(By.XPATH, './following-sibling::*[1]')
                except Exception:
                    continue
                text = element_text(value_elem)
                if text:
                    return text
        except Exception:
            continue

    # 兜底：原有选择器 + 法语/英语 XPath
    selectors = [
        '.projects-expiry-value',
        '.projects-service-cell--expiry strong',
        '[class*="expiry"] strong',
        '[class*="expiry"] [class*="value"]',
        '[class*="expiry"]',
        '[class*="expire"]',
        '[class*="Expires"]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expire dans")]/following-sibling::div[1]',
        './/*[contains(translate(normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expires in")]/following-sibling::div[1]',
        './/*[contains(normalize-space(.), "过期") or contains(normalize-space(.), "到期")]',
    ]
    for selector in selectors:
        try:
            for elem in find_elements(card, selector):
                text = element_text(elem)
                date_text = extract_date_like(text)
                if date_text:
                    return date_text
                duration_text = extract_duration_like(text)
                if duration_text:
                    return duration_text
                if text and len(text) <= 120:
                    return text
        except Exception:
            continue

    card_text = element_text(card)
    return extract_date_like(card_text) or extract_duration_like(card_text) or '未知'


def get_renewal_available_note(card):
    text = element_text(card)
    patterns = [
        r'Renewal\s+will\s+be\s+available[^\n]*',
        r'Renouvellement\s+(?:sera\s+)?disponible[^\n]*',
        r'可续期[^\n]*',
        r'续期[^\n]*前[^\n]*',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(0).strip()
    return ''


def get_card_by_index(sb, idx):
    cards = find_project_cards(sb)
    if 1 <= idx <= len(cards):
        return cards[idx - 1]
    return None


def wait_for_renew_result(sb, idx, timeout=30):
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # ★ 多语言成功弹窗：successfully / avec succès / réussi / succès
            for kw in SUCCESS_KEYWORDS:
                success_modals = sb.driver.find_elements(
                    By.XPATH,
                    f'//div[contains(@class, "modal") and '
                    f'contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÉÈÊËÎÏÔÙÛÜÇ", "abcdefghijklmnopqrstuvwxyzàâéèêëîïôùûüç"), "{kw.lower()}")]',
                )
                if any(modal.is_displayed() for modal in success_modals):
                    card = get_card_by_index(sb, idx)
                    return True, get_project_expiry(card) if card else '未知', 'success modal'

            card = get_card_by_index(sb, idx)
            if card:
                renewal_note = get_renewal_available_note(card)
                renew_buttons = find_renew_buttons(card)
                if renewal_note and not renew_buttons:
                    return True, get_project_expiry(card), renewal_note
        except Exception as e:
            print(f"检查续期结果时暂时失败: {e}")

        sb.sleep(1)

    card = get_card_by_index(sb, idx)
    note = get_renewal_available_note(card) if card else ''
    expiry = get_project_expiry(card) if card else '未知'
    return False, expiry, note


def get_renew_note(card):
    selectors = [
        '.projects-renew-note',
        '[class*="renew-note"]',
        '[class*="note"]',
        '[class*="tip"]',
    ]
    for selector in selectors:
        try:
            for elem in card.find_elements(By.CSS_SELECTOR, selector):
                text = element_text(elem)
                if text:
                    return text
        except Exception:
            continue
    return '未到续期时间'


def get_action_button_label(button):
    text = element_text(button)
    for attr in ('aria-label', 'title'):
        try:
            value = (button.get_attribute(attr) or '').strip()
        except Exception:
            value = ''
        if value:
            text = f"{text} {value}"
    lowered = text.lower()
    # ★ 支持法语 réactiver / reactiver
    if any(kw in lowered for kw in ('reactivate', 'réactiver', 'reactiver')) \
            or '重新激活' in text or '恢复' in text:
        return 'Reactivate'
    return 'Renew'


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


def has_renew_antibot_modal(sb):
    selectors = [
        '//div[contains(., "Anti-bot confirmation")]',
        '//div[contains(., "Confirm you are human")]',
        '//div[contains(., "I am not a robot")]',
    ]
    for selector in selectors:
        try:
            if any(elem.is_displayed() for elem in sb.driver.find_elements(By.XPATH, selector)):
                return True
        except Exception:
            continue
    return False


def click_captcha_checkbox(sb, label='验证码', timeout=10):
    """点击 ACLClouds 页面上的人机验证复选框，并处理图形验证码挑战。"""
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
    """处理图形验证码挑战：先等待挑战加载，再尝试点击对应图像。"""
    start_time = time.time()
    challenge = None
    last_error = None
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
        print(f"{label} 等待验证码挑战加载超时: {last_error}")
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
            print(f"{label} 当前挑战没有可点击选项，重试中...")
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


# ★ 消息里补上项目名和旧过期时间，排查更方便
def build_success_message(project_name, old_expiry, new_expiry):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "✅ 续期成功",
        f"📦 项目: {project_name}",
        f"⏱️ 旧过期: {old_expiry}",
        f"⏱️ 新过期: {new_expiry}",
        f"👤 登录账户: {masked_email}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ]
    return "\n".join(lines)


def build_not_yet_due_message(project_name, expiry):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        "⏳ 未到续期时间",
        f"📦 项目: {project_name}",
        f"⏱️ 当前过期时间: {expiry}",
        f"👤 登录账户: {masked_email}",
        f"⏱️ 运行时间: {beijing_time_str()}",
    ]
    return "\n".join(lines)


def build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note):
    masked_email = mask_email(EMAIL)
    lines = [
        "🇫🇷 Aclclouds 续期通知",
        "",
        f"❌ 续期状态未确认: {project_name}",
        f"👤 登录账户: {masked_email}",
    ]
    if old_expiry and old_expiry.lower() not in ['suspended', 'paused', 'suspendu', 'en pause', '暂停']:
        lines.append(f"旧过期: {old_expiry}")
    lines.extend([
        f"当前过期: {new_expiry}",
        f"页面提示: {result_note or '未发现成功提示'}",
    ])
    return "\n".join(lines)


def handle_renew_antibot(sb, project_name):
    """Renew 后如果弹出 Anti-bot confirmation，则点击确认。"""
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
        if label == '密码':
            print(f"JS 赋值后{label}长度: {len(entered_value)}")
        else:
            print(f"JS 赋值后{label}值: '{entered_value}'")

    return entered_value == value


def login(sb, email, password):
    """执行登录，返回是否成功"""
    print("开始登录流程...")

    if not fill_input(sb, '#username', email, '邮箱'):
        print("⚠️ 邮箱仍未能正确填入，可能页面有动态行为。")

    if not fill_input(sb, '#password', password, '密码'):
        print("⚠️ 密码仍未能正确填入。")

    captcha_ok = click_captcha_checkbox(sb, '登录验证码')
    if not captcha_ok:
        print("⚠️ 登录验证码未完成，暂不点击登录按钮，避免直接提交。")
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
        print("所有选择器失败，使用 JS 点击")
        sb.execute_script('''
            var els = document.querySelectorAll('div, button, a');
            for (var el of els) {
                if (el.textContent.trim() === 'Sign in') {
                    el.click();
                    return true;
                }
            }
            return false;
        ''')

    try:
        wait_for_url_change(sb, login_page_url, timeout=30)
        if '/auth/login' not in sb.get_current_url():
            sb.assert_title('Home | ACLClouds')
            print("✅ 登录成功！")
            return True
        else:
            error_msg = ""
            try:
                errors = sb.driver.find_elements(By.CSS_SELECTOR, '.auth-error-text, .alert-danger, .error-message')
                error_msg = errors[0].text.strip() if errors else ''
            except Exception:
                pass
            print(f"❌ 登录失败，错误: {error_msg}")
            return False
    except Exception as e:
        print(f"登录过程异常: {e}")
        return False


def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        # ★ requests 不支持 socks://，规范化为 socks5h://
        normalized = proxy_server.replace("socks://", "socks5h://")
        proxies = {"http": normalized, "https": normalized}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()


def main():
    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.getenv('S5_PROXY') or os.getenv('PROXY_SERVER') or "socks5://127.0.0.1:1080"
    # ★ 规范化为 socks5h://
    if PROXY_SERVER.startswith("socks://"):
        PROXY_SERVER = PROXY_SERVER.replace("socks://", "socks5h://", 1)

    # ★ headless 由环境变量控制（CI 里设 HEADLESS=true）
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

        if not is_login_page(sb):
            sb.open(BASE_URL)
            sb.wait_for_ready_state_complete()
            time.sleep(2)

        if is_login_page(sb):
            print("执行正常登录...")
            if not EMAIL or not PASSWORD:
                print("❌ 未配置 EMAIL 或 PASSWORD，无法执行账号密码登录。")
                send_telegram("⚠️ 未配置 EMAIL 或 PASSWORD。")
                return
            if not login(sb, EMAIL, PASSWORD):
                return
        elif is_logged_in(sb):
            print(f"✅ 当前已登录。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
        else:
            print(f"❌ 未能确认登录状态。URL: {sb.get_current_url()}，标题: {sb.get_title()}")
            send_telegram("⚠️ 未能确认登录状态，请检查账号密码配置。")
            return

        # 2. 进入项目页
        sb.open(PROJECTS_URL)
        sb.wait_for_ready_state_complete()
        time.sleep(3)

        # 3. 定位卡片
        cards = find_project_cards(sb)

        if not cards:
            print("❌ 未找到项目卡片。")
            log_projects_page_diagnostics(sb)
            send_telegram("⚠️ 未找到项目卡片，请检查页面结构。")
            return

        print(f"找到 {len(cards)} 个项目卡片。")
        for idx, card in enumerate(cards, 1):
            try:
                project_name = get_project_name(card, idx)
                old_expiry = get_project_expiry(card)
                print(f"[{project_name}] 当前过期: {old_expiry}")

                renew_btn = find_renew_buttons(card)

                if renew_btn:
                    action_label = get_action_button_label(renew_btn[0])
                    safe_click_element(sb, renew_btn[0], f"[{project_name}] {action_label}按钮")
                    print(f"[{project_name}] 点击 {action_label}...")
                    handle_renew_antibot(sb, project_name)
                    success, new_expiry, result_note = wait_for_renew_result(sb, idx, timeout=30)
                    if success:
                        print(f"续期成功！状态: {result_note}，新过期: {new_expiry}")
                        send_telegram(build_success_message(project_name, old_expiry, new_expiry))
                    else:
                        send_telegram(build_unconfirmed_message(project_name, old_expiry, new_expiry, result_note))
                else:
                    note = get_renew_note(card)
                    print(f"无 Renew 按钮，提示: {note}")
                    send_telegram(build_not_yet_due_message(project_name, old_expiry))
            except Exception as e:
                print(f"处理卡片 {idx} 出错: {e}")
                send_telegram(f"🇫🇷 Aclclouds 续期通知\n\n⚠️ 处理出错: {str(e)}")

        print("所有项目处理完成。")


if __name__ == '__main__':
    main()
