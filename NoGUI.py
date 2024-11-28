import time
import requests
import json
import re
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import random

class EmailManager:
    def __init__(self):
        self.email_queues = {}
        self.email_queues = {}
        self.lock = threading.Lock()

    def get_queue(self, thread_id):
        with self.lock:
            if (thread_id not in self.email_queues):
                self.email_queues[thread_id] = queue.Queue()
            return self.email_queues[thread_id]

    def delete_email(self, email, token):
        try:
            requests.delete(
                "https://api.mail.tm/accounts",
                headers={"Authorization": f"Bearer {token}"}
            )
        except Exception as e:
            print(f"Error deleting email {email}: {e}")

class EmailPool:
    def __init__(self):
        self.email_queue = queue.Queue()
        self.lock = threading.Lock()

    def add_email(self, email, token):
        self.email_queue.put((email, token))

    def get_email(self):
        try:
            return self.email_queue.get(timeout=30)  # Wait up to 30 seconds for an email
        except queue.Empty:
            return None, None

    def get_size(self):
        return self.email_queue.qsize()

class EmailServiceBase:
    def create_email(self):
        raise NotImplementedError()

class MailTmService(EmailServiceBase):
    def __init__(self):
        self.base_delay = 15  # Increased from 10 to 15
        self.domains = []
        self.last_domain_update = 0
        self.domain_update_interval = 300  # Update domains every 5 minutes
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })
        self.base_url = "https://api.mail.tm"
        self.last_domain = None  # Add cache for last working domain

    def _update_domains(self):
        if time.time() - self.last_domain_update > self.domain_update_interval:
            try:
                response = self.session.get(f"{self.base_url}/domains")
                if response.status_code == 200:
                    json_data = response.json()
                    
                    # Handle both array and hydra:member formats
                    domains_data = json_data if isinstance(json_data, list) else json_data.get('hydra:member', [])
                    
                    # Extract domains and validate
                    self.domains = []
                    for domain_obj in domains_data:
                        if isinstance(domain_obj, dict) and domain_obj.get('domain'):
                            self.domains.append(domain_obj['domain'])
                    
                    if self.domains:
                        print(f"Successfully updated domains. Available: {self.domains[:3]}")
                        self.last_domain_update = time.time()
                        self.last_domain = self.domains[0]  # Cache first domain
                    else:
                        print("No valid domains in response. Data:", json_data)
                else:
                    print(f"Domain request failed: {response.status_code}")
                    # Use cached domain if available
                    if self.last_domain:
                        self.domains = [self.last_domain]
                        print(f"Using cached domain: {self.last_domain}")
            except Exception as e:
                print(f"Domain update error: {str(e)}")
                # Use cached domain in case of error
                if self.last_domain:
                    self.domains = [self.last_domain]
                    print(f"Using cached domain: {self.last_domain}")
                time.sleep(self.base_delay)

    def create_email(self):
        self._update_domains()
        if not self.domains:
            time.sleep(self.base_delay)
            return None, None

        for domain in self.domains:
            try:
                email_address = f"user{int(time.time())}{random.randint(1000,9999)}@{domain}"
                payload = {
                    "address": email_address,
                    "password": "SecurePassword123"
                }
                
                # Create account with retry
                for _ in range(2):
                    account_response = self.session.post(
                        "https://api.mail.tm/accounts", 
                        json=payload,
                        timeout=10
                    )
                    
                    if account_response.status_code == 201:
                        time.sleep(2)  # Small delay between account creation and token request
                        
                        # Get token
                        token_response = self.session.post(
                            "https://api.mail.tm/token",
                            json=payload,
                            timeout=10
                        )
                        
                        if token_response.status_code == 200:
                            token = token_response.json().get("token")
                            if token:
                                return email_address, token
                            
                    elif account_response.status_code == 429:
                        time.sleep(self.base_delay)
                        continue
                        
                    time.sleep(5)
                    
            except Exception as e:
                print(f"Email creation error: {e}")
                time.sleep(5)
                continue
                
        time.sleep(self.base_delay)
        return None, None

class TempMailOrgService(EmailServiceBase):
    def __init__(self):
        self.session = requests.Session()
        self.base_url = "https://temp-mail.org"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0"
        }
        self.retries = 3
        self.retry_delay = 5

    def create_email(self):
        for attempt in range(self.retries):
            try:
                # Step 1: Initialize session with proper headers
                init_response = self.session.get(
                    f"{self.base_url}/en",
                    headers=self.headers,
                    timeout=10,
                    allow_redirects=True
                )
                
                if init_response.status_code == 403:
                    print(f"TempMailOrg blocked (attempt {attempt + 1}/{self.retries})")
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                
                if init_response.status_code != 200:
                    print(f"TempMailOrg init failed: {init_response.status_code}")
                    continue

                # Update session cookies and headers
                self.session.headers.update({
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/en"
                })

                # Rest of the implementation remains the same
                # ...existing code...

            except requests.exceptions.RequestException as e:
                print(f"TempMailOrg network error (attempt {attempt + 1}/{self.retries}): {str(e)}")
                if attempt < self.retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                continue
            except Exception as e:
                print(f"TempMailOrg unexpected error: {str(e)}")
                return None, None

        print("TempMailOrg: All retries failed")
        return None, None

class TempMailBoxService(EmailServiceBase):
    def create_email(self):
        try:
            # Get domain list
            response = requests.get("https://api.tempmail.box/domains")
            if response.status_code != 200:
                return None, None
                
            domain = response.json()[0]
            email = f"user{int(time.time())}@{domain}"
            
            # Create mailbox
            create_response = requests.post(
                "https://api.tempmail.box/email",
                json={"email": email}
            )
            
            if create_response.status_code != 201:
                return None, None
                
            data = create_response.json()
            return email, data.get("token")
        except Exception as e:
            print(f"TempMailBox error: {e}")
            return None, None

class MinuteInboxService(EmailServiceBase):
    def create_email(self):
        try:
            session = requests.Session()
            response = session.post(
                "https://www.minuteinbox.com/index/index",
                headers={"X-Requested-With": "XMLHttpRequest"}
            )
            
            if response.status_code != 200:
                return None, None
                
            data = response.json()
            email = data.get("email")
            token = session.cookies.get("PHPSESSID")  # Session ID as token
            return email, token
        except Exception as e:
            print(f"MinuteInbox error: {e}")
            return None, None

class TenMinuteMailService(EmailServiceBase):
    def create_email(self):
        try:
            response = requests.get("https://10minutemail.net/address.api.php")
            if response.status_code != 200:
                return None, None
            data = response.json()
            return data.get("mail_get_mail"), data.get("session_id")
        except Exception as e:
            print(f"10MinuteMail error: {e}")
            return None, None

class TempMailIoService(EmailServiceBase):
    def create_email(self):
        try:
            response = requests.post(
                "https://temp-mail.io/api/v3/email/new",
                headers={"Content-Type": "application/json"}
            )
            if response.status_code != 200:
                return None, None
            data = response.json()
            return data.get("email"), data.get("token")
        except Exception as e:
            print(f"TempMailIo error: {e}")
            return None, None

class DropMailMeService(EmailServiceBase):
    def create_email(self):
        try:
            response = requests.post("https://dropmail.me/api/graphql", json={
                "query": """
                mutation {
                    introduceSession {
                        id
                        addresses {
                            address
                        }
                    }
                }
                """
            })
            if response.status_code != 200:
                return None, None
            data = response.json()
            session = data.get("data", {}).get("introduceSession", {})
            email = session.get("addresses", [{}])[0].get("address")
            token = session.get("id")
            return email, token
        except Exception as e:
            print(f"DropMail error: {e}")
            return None, None

class MohmalService(EmailServiceBase):
    def create_email(self):
        try:
            session = requests.Session()
            response = session.get("https://www.mohmal.com/en/api/create/random")
            if response.status_code != 200:
                return None, None
            
            data = response.json()
            if data.get('success'):
                email = data['email']
                token = data['id']
                return email, token
            return None, None
        except Exception as e:
            print(f"Mohmal error: {e}")
            return None, None

class MailboxOrgService(EmailServiceBase):
    def create_email(self):
        try:
            response = requests.post(
                "https://api.mailbox.org/v1/temp/create",
                headers={"Accept": "application/json"}
            )
            if response.status_code != 201:
                return None, None
            data = response.json()
            return data.get("email"), data.get("access_token")
        except Exception as e:
            print(f"MailboxOrg error: {e}")
            return None, None

class GeneralEmailService(EmailServiceBase):
    def create_email(self):
        try:
            session = requests.Session()
            response = session.get(
                "https://generator.email/api/v1/create",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if response.status_code != 200:
                return None, None
            data = response.json()
            return data.get("email"), session.cookies.get("_email_session")
        except Exception as e:
            print(f"GeneralEmail error: {e}")
            return None, None

def get_verification_link(token, email):
    try:
        # Get mail service based on email domain
        if '@mail.tm' in email:
            return get_mail_tm_link(token)
        elif '@mailbox.org' in email:
            return get_mailbox_link(token)
        else:
            # Default to mail.tm for unknown domains
            return get_mail_tm_link(token)
    except Exception as e:
        print(f"Error getting verification link: {e}")
        return None

def get_mail_tm_link(token):
    try:
        # Получаем список сообщений на временной почте
        messages_response = requests.get(
            "https://api.mail.tm/messages", 
            headers={"Authorization": f"Bearer {token}"}
        )
        if messages_response.status_code != 200:
            print(f"Ошибка получения сообщений: {messages_response.status_code}")
            return None

        messages = messages_response.json().get('hydra:member', [])
        if not messages:
            print("Нет сообщений для проверки.")
            return None

        # Предположим, что первое сообщение содержит нужную ссылку для подтверждения
        message = messages[0]  # Берем первое сообщение
        message_id = message['id']

        # Получаем содержимое сообщения
        message_response = requests.get(
            f"https://api.mail.tm/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        if message_response.status_code != 200:
            print(f"Ошибка получения сообщения: {message_response.status_code}")
            return None

        message_data = message_response.json()
        content = message_data.get('text', '')
        
        # Используем регулярное выражение для поиска ссылки подтверждения
        match = re.search(r'https://www\.juicychat\.ai/yume/api/emailLoginBack\?param=[a-f0-9]{32}', content)
        if match:
            return match.group(0)
        else:
            print("Не найдена ссылка подтверждения в сообщении.")
            return None
    except Exception as e:
        print(f"Произошла ошибка при извлечении ссылки подтверждения: {e}")
        return None

def get_mailbox_link(token):
    try:
        response = requests.get(
            "https://api.mailbox.org/v1/messages",
            headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code != 200:
            return None
            
        messages = response.json()
        for msg in messages:
            content = msg.get('text', '')
            match = re.search(r'https://www\.juicychat\.ai/yume/api/emailLoginBack\?param=[a-f0-9]{32}', content)
            if match:
                return match.group(0)
        return None
    except Exception as e:
        print(f"MailboxOrg fetch error: {e}")
        return None

# Основная функция регистрации с учетом модального окна
def register_on_juicychat(driver, email, token):
    # Modified version of existing register_on_juicychat
    # Remove email/token generation as they're now passed as parameters
    try:
        print(f"Using email: {email}")
        # Шаг 1: Перейти на сайт JuicyChat
        url = "https://www.juicychat.ai/"
        driver.get(url)
        time.sleep(3)

        # Шаг 2: Закрытие модального окна (если оно присутствует)
        try:
            close_button = driver.find_element(By.CSS_SELECTOR, "button.ant-modal-close")
            ActionChains(driver).move_to_element(close_button).click(close_button).perform()
            print("Модальное окно закрыто.")
            time.sleep(2)
        except Exception as e:
            print("Модальное окно не найдено или не требуется закрытие.")

        # Шаг 3: Нажать кнопку "Sign in"
        sign_in_button = driver.find_element(By.CSS_SELECTOR, "div._noLogin_n1hj1_256")
        ActionChains(driver).move_to_element(sign_in_button).click(sign_in_button).perform()
        print("Кнопка 'Sign in' нажата.")
        time.sleep(3)

        # Шаг 4: Явное ожидание кнопки "or continue in with email"
        wait = WebDriverWait(driver, 10)  # Ждем до 10 секунд
        continue_with_email_button = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div._other_4ay55_53"))
        )
        ActionChains(driver).move_to_element(continue_with_email_button).click(continue_with_email_button).perform()
        print("Кнопка 'or continue in with email' нажата.")
        time.sleep(3)

        # Шаг 5: Ввод временной почты
        email_input = driver.find_element(By.CSS_SELECTOR, "input.ant-input._emailInput_4ay55_94")
        email_input.clear()
        email_input.send_keys(email)
        print(f"Email {email} введен.")

        # Шаг 6: Нажать кнопку подтверждения
        submit_button = driver.find_element(By.CSS_SELECTOR, "div._buttonWrapper_1qptm_1._confirm_1qptm_21._submit_4ay55_120")
        ActionChains(driver).move_to_element(submit_button).click(submit_button).perform()
        print("Кнопка подтверждения нажата.")
        time.sleep(5)

        # Шаг 7: Проверка почты на наличие письма
        print("Ожидание письма для подтверждения...")
        for _ in range(10):  # Проверяем почту каждые 10 секунд (максимум 10 раз)
            verification_link = get_verification_link(token, email)  # Pass email parameter
            if verification_link:
                print(f"Найдена ссылка подтверждения: {verification_link}")
                driver.get(verification_link)
                print("��очта подтверждена.")
                break
            time.sleep(10)
        else:
            print("Не удалось получить письмо с подтверждением.")
            return

        # Шаг 8: Переход на страницу /bonus
        print("Переход на страницу /bonus...")
        driver.get("https://www.juicychat.ai/bonus")
        time.sleep(3)

        # Шаг 9: Ввод кода приглашения
        invite_code_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.ant-input._inviteCodeInput_1c3j0_267"))
        )
        invite_code_input.clear()
        invite_code_input.send_keys(INVITE_CODE)  # Use global variable
        print(f"Код {INVITE_CODE} введен.")

        claim_button = driver.find_element(By.CSS_SELECTOR, "div._taskItemBtn_1c3j0_288")
        ActionChains(driver).move_to_element(claim_button).click(claim_button).perform()
        print("Кнопка 'Claim' нажата.")
        time.sleep(3)

        print("Процесс завершен.")
        
    except Exception as e:
        print(f"Произошла ошибка: {e}")
        raise

def email_service_worker(service, email_pool):
    retry_delay = 5
    while True:
        try:
            if email_pool.get_size() < 30:  # Increased from 20 to 30
                email, token = service.create_email()
                if email and token:
                    email_pool.add_email(email, token)
                    print(f"Added email to pool from {service.__class__.__name__}: {email}")
                    time.sleep(8)  # Reduced from 10 to 8 seconds
                else:
                    print(f"Failed to create email with {service.__class__.__name__}")
                    time.sleep(retry_delay)
            else:
                time.sleep(1)  # Reduced from 2 to 1 second
        except Exception as e:
            print(f"Service error {service.__class__.__name__}: {str(e)}")
            time.sleep(retry_delay)

# Update the services list to reduce TempMailOrg instances since it's less reliable
def start_email_services(email_pool):
    services = []
    
    # Primary service (multiple instances)
    for _ in range(18):  # Increased MailTm instances
        services.append(MailTmService())
        time.sleep(1)
    
    # Only one TempMailOrg instance as backup
    services.append(TempMailOrgService())
    
    service_threads = []
    for service in services:
        thread = threading.Thread(
            target=email_service_worker,
            args=(service, email_pool),
            daemon=True
        )
        thread.start()
        service_threads.append(thread)
        time.sleep(1)  # Spread out thread starts
    return service_threads

def worker(thread_id, email_pool):
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("--disable-web-security")
    options.add_argument("--allow-insecure-localhost")
    options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.set_page_load_timeout(30)

    try:
        while True:
            email, token = email_pool.get_email()
            if not email or not token:
                print(f"Thread {thread_id}: Waiting for available email...")
                time.sleep(5)
                continue

            try:
                register_on_juicychat(driver, email, token)
                # Clear only juicychat.ai cookies
                all_cookies = driver.get_cookies()
                for cookie in all_cookies:
                    if '.juicychat.ai' in cookie.get('domain', ''):
                        driver.delete_cookie(cookie['name'])
                print(f"Thread {thread_id}: Cleared juicychat.ai cookies")
            except Exception as e:
                print(f"Thread {thread_id} error: {e}")
            finally:
                # Delete used email
                try:
                    requests.delete(
                        "https://api.mail.tm/accounts",
                        headers={"Authorization": f"Bearer {token}"}
                    )
                    print(f"Deleted used email: {email}")
                except Exception as e:
                    print(f"Error deleting email {email}: {e}")
            
            time.sleep(5)
    except Exception as e:
        print(f"Worker thread {thread_id} error: {e}")
    finally:
        driver.quit()

def initialize_email_pool(email_pool, target_size=30, timeout=60):  # Increased timeout
    """Generate initial pool of emails with timeout"""
    print(f"Initializing email pool. Target size: {target_size}, Timeout: {timeout}s")
    start_time = time.time()
    
    # Start email service workers
    email_service_threads = start_email_services(email_pool)
    
    # Wait until pool is filled or timeout
    retries = 0
    max_retries = 3
    while retries < max_retries:
        current_size = email_pool.get_size()
        print(f"Current pool size: {current_size}/{target_size}")
        
        if current_size >= target_size:
            break
            
        if time.time() - start_time > timeout:
            print("Pool initialization timeout. Retrying...")
            start_time = time.time()
            retries += 1
            # Force domain refresh
            for service in email_service_threads:
                if isinstance(service, MailTmService):
                    service.last_domain_update = 0
        
        time.sleep(2)
    
    final_size = email_pool.get_size()
    print(f"Email pool initialized with {final_size} emails")
    if final_size == 0:
        print("Warning: No emails were created. Check service connectivity.")
    return email_service_threads

if __name__ == "__main__":
    num_threads = int(input("Enter number of parallel threads: "))
    INVITE_CODE = input("Enter invite code: ")  # Request invite code
    email_pool = EmailPool()
    
    # Initialize email pool first
    email_service_threads = initialize_email_pool(email_pool)
    
    print("Starting worker threads...")
    # Start worker threads
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [
            executor.submit(worker, i, email_pool)
            for i in range(num_threads)
        ]
        
        try:
            for future in futures:
                future.result()
        except KeyboardInterrupt:
            print("Script stopped by user.")