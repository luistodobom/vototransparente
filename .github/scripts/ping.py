#!/usr/bin/env python3
"""
Script to keep Streamlit app alive by visiting it periodically.
This prevents the app from going to sleep on free hosting tiers.
"""

import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_driver():
    """Create a headless Chrome driver with appropriate options."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    
    try:
        # Use webdriver-manager to automatically handle ChromeDriver installation
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        return driver
    except Exception as e:
        logger.error(f"Failed to create Chrome driver: {e}")
        raise

def ping_website(url, timeout=30):
    """
    Visit the website and wait for it to load.
    
    Args:
        url (str): The URL to visit
        timeout (int): Maximum time to wait for page load
    
    Returns:
        bool: True if successful, False otherwise
    """
    driver = None
    try:
        logger.info(f"Creating Chrome driver...")
        driver = create_driver()
        
        logger.info(f"Navigating to {url}")
        driver.get(url)
        
        # Wait for the page to load (look for Streamlit elements)
        wait = WebDriverWait(driver, timeout)
        
        # Wait for either the Streamlit app to load or any content to appear
        try:
            # Try to wait for Streamlit-specific elements first
            wait.until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            logger.info("Page loaded successfully")
            
            # Additional wait to ensure Streamlit app is fully initialized
            time.sleep(5)
            
            # Get page title for confirmation
            title = driver.title
            logger.info(f"Page title: {title}")
            
            # Take a screenshot for debugging (optional)
            # driver.save_screenshot("/tmp/streamlit_ping.png")
            
            return True
            
        except TimeoutException:
            logger.warning(f"Timeout waiting for page to load after {timeout} seconds")
            # Even if timeout, the request might have been enough to wake up the app
            return True
            
    except WebDriverException as e:
        logger.error(f"WebDriver error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("Chrome driver closed")
            except Exception as e:
                logger.error(f"Error closing driver: {e}")

def main():
    """Main function to ping the Streamlit app."""
    url = "https://vototransparente.streamlit.app/"
    
    logger.info("Starting Streamlit app ping...")
    logger.info(f"Target URL: {url}")
    
    success = ping_website(url)
    
    if success:
        logger.info("✅ Successfully pinged the Streamlit app")
        exit(0)
    else:
        logger.error("❌ Failed to ping the Streamlit app")
        exit(1)

if __name__ == "__main__":
    main()