# This Script is for running the task everyday using Windows Task Scheduler

# Dependencies
# Selenium modules for controlling Chrome browser
from selenium.webdriver import Chrome  # For initializing and controlling the Chrome browser
from selenium import webdriver  # Provides access to the webdriver, allowing interaction with web browsers
from selenium.webdriver.chrome.options import Options  # For configuring Chrome browser options (e.g., headless mode)
from selenium.webdriver.chrome.service import Service  # For managing the ChromeDriver service (e.g., starting, stopping)

# Selenium modules for interacting with web elements
from selenium.webdriver.common.by import By  # For locating elements on a webpage (e.g., By.ID, By.XPATH)
from selenium.webdriver.support.ui import Select  # For interacting with <select> HTML elements (dropdowns)
from selenium.webdriver.support.ui import WebDriverWait  # For implementing explicit waits until a condition is met
from selenium.webdriver.support import expected_conditions as EC  # For defining conditions to wait for (e.g., element visibility)

# Other useful libraries
from fake_useragent import UserAgent  # For generating random user agents to mimic different browsers
import time  # For adding delays (e.g., time.sleep) during the script execution
import requests  # For making HTTP requests to interact with websites directly without using a browser
from bs4 import BeautifulSoup  # For parsing and extracting data from HTML content
import pandas as pd  # For data manipulation, analysis, and creating DataFrames
import re


# Connect to MongoDB
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import os
from dotenv import load_dotenv

load_dotenv()
db_username = os.getenv("db_username")
db_password = os.getenv("db_password")
db_host = os.getenv("db_host")

if not all([db_username, db_password, db_host]):
    print("Please set db_username, db_password, and db_host in your .env file")
else:
    uri = f"mongodb+srv://{db_username}:{db_password}@{db_host}"
    client = MongoClient(uri, server_api=ServerApi('1'))

    try:
        client.admin.command('ping')
        print("Pinged your deployment. You successfully connected to MongoDB!")
        
        # Create/Select database and collection
        db = client["finance_news_db"] 
        collection = db["numerous_articles"]
        print(f"Connected to database: {db.name}, collection: {collection.name}")
        
    except Exception as e:
        print(e)


# Working Scraper!
news_uri = "https://finance.yahoo.com/topic/latest-news/"

# We use Selenium because the 'fin-streamer' percentages are loaded via JavaScript
options = Options()
options.add_argument("--headless=new") 
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-extensions")
options.page_load_strategy = 'eager'

# Block images and stylesheets to save memory
prefs = {
    "profile.managed_default_content_settings.images": 2,
    "profile.managed_default_content_settings.stylesheets": 2,
}
options.add_experimental_option("prefs", prefs)

ua = UserAgent()
userAgent = ua.random
options.add_argument(f'user-agent={userAgent}')

driver = webdriver.Chrome(options=options)
driver.set_page_load_timeout(60)
driver.set_script_timeout(60)

# Headers for the requests library
headers = {'User-Agent': userAgent}

try:
    print("Fetching news list via Selenium...")
    driver.get(news_uri)
    time.sleep(3) # Wait for page to load
    
    # Scroll to load more news (Limit to 3 scrolls for stability)
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_count = 0
    max_scrolls = 8
    
    while scroll_count < max_scrolls:
        try:
            # Scroll to bottom
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            
            # Wait for page to load
            time.sleep(3) 
            
            # Calculate new scroll height
            new_height = driver.execute_script("return document.body.scrollHeight")
            
            if new_height == last_height:
                print("Reached the end of the news feed.")
                break
                
            last_height = new_height
            scroll_count += 1
            print(f"Scrolled {scroll_count}/{max_scrolls} times...")
        except Exception as e:
            print(f"Scrolling interrupted: {e}. Proceeding with current content.")
            break
        
    page_source = driver.page_source
finally:
    driver.quit()

news_soup = BeautifulSoup(page_source, 'html.parser')

news_list = news_soup.find('ul', class_= "stream-items yf-9xydx9")
news_listings = news_list.find_all('li', class_= "stream-item story-item yf-9xydx9")
print(f"Found {len(news_listings)} articles.")

for news in news_listings:
    news_data = {}
    # From the main listing page: want to scrape title, link, publisher, tickers(???)
    # Title
    title = news.find('h3')
    if not title: continue # Skip if malformed
    news_data['title'] = title.text
    
    # Publisher
    publisher = news.find('div', class_=re.compile(r'publishing.*'))
    news_data['publisher'] = publisher.text.split('•')[0] if publisher else "Unknown"
    
    # Tickers (static)
    tickers = []
    if news.find('div', class_='taxonomy-links'):  
        for ticker in news.find('div', class_='taxonomy-links').find_all('span', class_=re.compile(r'ticker-wrapper.*')):
            
            streamer = ticker.find('fin-streamer', {'data-field': 'regularMarketChangePercent'})
            if streamer:
                symbol = streamer.get('data-symbol')
                change = streamer.get_text(strip=True)
                
                # Fallback to static if text is empty
                if not change:
                    change = None
                tickers.append({'symbol': symbol, 'change': change})
            else:
                tickers.append({'symbol': ticker.text.strip(), 'change': None})     
    news_data['tickers'] = tickers
    
    # Link
    link = news.find('a')['href']
    if not link: continue # If link is missing, skip this article
    news_data['link'] = link
    
    # Fetch Article Details 
    try:
        news_detail_url = news_data['link']
        news_detail_page = requests.get(news_detail_url, headers=headers)
        news_detail_soup = BeautifulSoup(news_detail_page.content, 'html.parser')
        
        # Author
        author = news_detail_soup.find('div', class_=re.compile(r'byline-attr-author.*'))
        news_data['authors'] = author.text.strip() if author else "Unknown"

        # Time
        time = news_detail_soup.find('time')
        news_data['time_published'] = time.text if time else "Unknown"
        
        # Content
        content_text = []
        body_wrapper = news_detail_soup.find('div', class_='bodyItems-wrapper')
        seen_texts = set()
        
        if body_wrapper:
            for para in body_wrapper.find_all('p'):
                text = para.text.strip()
                if not text or text in seen_texts: continue
                if "Most Read from" in text or "Recommended Stories" in text: break
                if para.find_parent('div', class_='read-more-wrapper'): continue
                
                content_text.append(text)
                seen_texts.add(text)
                
        news_data['content'] = "\n".join(content_text)
        
        # Insert into MongoDB
        if 'collection' in globals():
            if collection.count_documents({'link': news_data['link']}) == 0:
                collection.insert_one(news_data)
                print(f"Inserted: {news_data['title']}")
            else:
                print(f"Skipped: {news_data['title']}")
        else:
            print(f"Scraped (DB not connected): {news_data['title']}")
            
    except Exception as e:
        print(f"Error scraping details for {news_data['title']}: {e}")
    