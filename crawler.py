
import os
import sys
import re
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple, Any
from argparse import ArgumentParser
import shutil
import logging
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
import urllib.request
import urllib.parse
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
import json
import time

URL = "https://hofman-photo.cz"

class AssetType(Enum):
    HTML = "html"
    CSS = "css"
    JS = "js"
    IMAGE = "image"
    OTHER = "other"

@dataclass
class Asset:
    url: str
    local_path: str
    asset_type: AssetType
    downloaded: bool = False

class WebCrawler(HTMLParser):
    def __init__(self, base_url: str, output_dir: str):
        super().__init__()
        self.base_url = base_url
        self.output_dir = Path(output_dir)
        self.visited_urls: Set[str] = set()
        self.assets: Dict[str, Asset] = {}
        self.pending_urls: Set[str] = set()
        
        # File extensions to download
        self.asset_extensions = {
            '.html': AssetType.HTML,
            '.htm': AssetType.HTML,
            '.css': AssetType.CSS,
            '.js': AssetType.JS,
            '.jpeg': AssetType.IMAGE,
            '.jpg': AssetType.IMAGE,
            '.png': AssetType.IMAGE,
            '.gif': AssetType.IMAGE,
            '.webp': AssetType.IMAGE,
            '.svg': AssetType.IMAGE,
            '.ico': AssetType.IMAGE
        }
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def normalize_url(self, url: str, base_url: str = None) -> str:
        """Normalize and resolve relative URLs"""
        if base_url is None:
            base_url = self.base_url
            
        # Handle relative URLs
        if url.startswith('//'):
            parsed_base = urllib.parse.urlparse(base_url)
            return f"{parsed_base.scheme}:{url}"
        elif url.startswith('/'):
            parsed_base = urllib.parse.urlparse(base_url)
            return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
        elif not url.startswith(('http://', 'https://')):
            return urllib.parse.urljoin(base_url, url)
        
        return url
    
    def get_asset_type(self, url: str) -> AssetType:
        """Determine asset type from URL"""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        
        for ext, asset_type in self.asset_extensions.items():
            if path.endswith(ext):
                return asset_type
                
        # Check if it's likely an HTML page (no extension or ends with /)
        if not path or path.endswith('/') or '.' not in Path(path).name:
            return AssetType.HTML
            
        return AssetType.OTHER
    
    def get_local_path(self, url: str) -> str:
        """Generate local file path for URL"""
        parsed = urllib.parse.urlparse(url)
        
        # Remove query parameters and fragments for file path
        path = parsed.path
        if not path or path == '/':
            path = '/index.html'
        elif path.endswith('/'):
            path = path + 'index.html'
        elif '.' not in Path(path).name:
            # No extension, assume HTML
            path = path + '.html'
            
        # Remove leading slash and create safe file path
        path = path.lstrip('/')
        local_path = self.output_dir / path
        
        # Ensure directory exists
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        return str(local_path)
    
    def should_crawl_url(self, url: str) -> bool:
        """Check if URL should be crawled (same domain)"""
        parsed_url = urllib.parse.urlparse(url)
        parsed_base = urllib.parse.urlparse(self.base_url)
        
        return parsed_url.netloc == parsed_base.netloc
    
    def download_file(self, url: str, local_path: str) -> bool:
        """Download a file from URL to local path"""
        try:
            self.logger.info(f"Downloading: {url}")
            
            # Create request with headers to mimic browser
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                with open(local_path, 'wb') as f:
                    shutil.copyfileobj(response, f)
                    
            self.logger.info(f"Downloaded: {local_path}")
            return True
            
        except (URLError, HTTPError, OSError) as e:
            self.logger.error(f"Failed to download {url}: {e}")
            return False
    
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]):
        """Parse HTML tags to find assets"""
        attr_dict = dict(attrs)
        
        # Find URLs in different tag attributes
        url_attrs = {
            'a': ['href'],
            'img': ['src', 'srcset'],
            'link': ['href'],
            'script': ['src'],
            'source': ['src', 'srcset'],
            'video': ['src', 'poster'],
            'audio': ['src'],
            'embed': ['src'],
            'object': ['data'],
            'iframe': ['src']
        }
        
        if tag in url_attrs:
            for attr in url_attrs[tag]:
                if attr in attr_dict:
                    url_value = attr_dict[attr]
                    
                    # Handle srcset (multiple URLs)
                    if attr == 'srcset':
                        urls = [url.strip().split()[0] for url in url_value.split(',')]
                    else:
                        urls = [url_value]
                    
                    for url in urls:
                        if url:
                            self.add_asset(url)
    
    def add_asset(self, url: str):
        """Add asset to download queue"""
        normalized_url = self.normalize_url(url)
        
        # Skip external URLs for non-HTML assets
        if not self.should_crawl_url(normalized_url):
            return
            
        # Skip if already processed
        if normalized_url in self.assets:
            return
            
        asset_type = self.get_asset_type(normalized_url)
        local_path = self.get_local_path(normalized_url)
        
        asset = Asset(
            url=normalized_url,
            local_path=local_path,
            asset_type=asset_type
        )
        
        self.assets[normalized_url] = asset
        
        # Add HTML pages to pending crawl list
        if asset_type == AssetType.HTML and normalized_url not in self.visited_urls:
            self.pending_urls.add(normalized_url)
    
    def crawl_page(self, url: str) -> bool:
        """Crawl a single HTML page"""
        if url in self.visited_urls:
            return True
            
        self.visited_urls.add(url)
        self.logger.info(f"Crawling page: {url}")
        
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                }
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.headers.get_content_type() != 'text/html':
                    return False
                    
                content = response.read().decode('utf-8', errors='ignore')
                
                # Parse HTML to find assets
                self.feed(content)
                
                # Save HTML file
                if url in self.assets:
                    with open(self.assets[url].local_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    self.assets[url].downloaded = True
                
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to crawl {url}: {e}")
            return False
    
    def download_assets(self):
        """Download all discovered assets"""
        for asset in self.assets.values():
            if not asset.downloaded and asset.asset_type != AssetType.HTML:
                success = self.download_file(asset.url, asset.local_path)
                asset.downloaded = success
                
                # Small delay to be respectful
                time.sleep(0.1)
    
    def crawl_site(self, max_pages: int = 100):
        """Main crawling method"""
        self.logger.info(f"Starting crawl of {self.base_url}")
        
        # Start with base URL
        self.add_asset(self.base_url)
        
        pages_crawled = 0
        while self.pending_urls and pages_crawled < max_pages:
            url = self.pending_urls.pop()
            if self.crawl_page(url):
                pages_crawled += 1
                
        self.logger.info(f"Crawled {pages_crawled} pages")
        self.logger.info(f"Found {len(self.assets)} total assets")
        
        # Download all assets
        self.download_assets()
        
        # Print summary
        self.print_summary()
    
    def print_summary(self):
        """Print crawling summary"""
        by_type = {}
        downloaded = 0
        
        for asset in self.assets.values():
            asset_type = asset.asset_type.value
            by_type[asset_type] = by_type.get(asset_type, 0) + 1
            if asset.downloaded:
                downloaded += 1
        
        self.logger.info("\n=== CRAWLING SUMMARY ===")
        self.logger.info(f"Total assets found: {len(self.assets)}")
        self.logger.info(f"Successfully downloaded: {downloaded}")
        
        for asset_type, count in by_type.items():
            self.logger.info(f"{asset_type.upper()}: {count}")
        
        self.logger.info(f"Output directory: {self.output_dir}")

def main():
    parser = ArgumentParser(description="Web crawler to download website assets")
    parser.add_argument("--url", default=URL, help="URL to crawl")
    parser.add_argument("--output", default="downloaded_site", help="Output directory")
    parser.add_argument("--max-pages", type=int, default=100, help="Maximum pages to crawl")
    
    args = parser.parse_args()
    
    crawler = WebCrawler(args.url, args.output)
    crawler.crawl_site(args.max_pages)

if __name__ == "__main__":
    main()