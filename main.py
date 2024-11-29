from typing import List, Dict, Optional, Any, TypedDict
import os
from dotenv import load_dotenv
import requests
import redis
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
import argparse
from dataclasses import dataclass

class Location(TypedDict):
    name: str

class Department(TypedDict):
    name: str
    jobs: List[Dict[str, Any]]
    children: Optional[List['Department']]

class Job(TypedDict):
    id: int
    title: str
    content: Optional[str]
    location: Location
    updated_at: str
    absolute_url: str
    company: Optional[str]

@dataclass
class ScannerConfig:
    use_redis: bool
    send_emails: bool
    title_keywords: List[str]

class MockRedis:
    """Mock Redis implementation for testing"""
    def __init__(self) -> None:
        self.seen_jobs: set[str] = set()
        
    def sismember(self, key: str, value: str) -> bool:
        return value in self.seen_jobs
        
    def sadd(self, key: str, value: str) -> None:
        self.seen_jobs.add(value)

class GreenhouseJobScanner:
    def __init__(self, config: ScannerConfig) -> None:
        self.email_address: str = os.getenv('EMAIL_ADDRESS', '')
        self.email_password: str = os.getenv('EMAIL_PASSWORD', '')
        self.title_keywords: List[str] = config.title_keywords
        self.config = config
        
        # Base URLs for Greenhouse API
        self.url_config = [
            {"url": "https://boards-api.greenhouse.io/v1/boards/deepmind",
             "company": "Deepmind"},
            {"url": "https://boards-api.greenhouse.io/v1/boards/anthropic",
             "company": "Anthropic"},
        ]
        
        # Redis setup - use mock if testing
        if config.use_redis:
            redis_url: str = os.getenv('REDISCLOUD_URL', '')
            self.redis = redis.from_url(redis_url)
        else:
            print("Using mock Redis for testing")
            self.redis = MockRedis()

    def fetch_jobs(self) -> List[Job]:
        """Fetch all jobs from Greenhouse API"""
        all_jobs: List[Job] = []
        for config in self.url_config:
            try:
                base_url = config['url']
                dept_response: requests.Response = requests.get(f"{base_url}/departments")
                dept_response.raise_for_status()
                departments: List[Department] = dept_response.json()['departments']
                
                for dept in departments:
                    # Handle jobs in the main department
                    if dept.get('jobs'):
                        for job in dept['jobs']:
                            job_copy = dict(job)
                            job_copy['company'] = config['company']
                            all_jobs.append(job_copy)
                    
                    # Handle jobs in child departments
                    if dept.get('children'):
                        for child in dept['children']:
                            if child.get('jobs'):
                                for job in child['jobs']:
                                    job_copy = dict(job)
                                    job_copy['company'] = config['company']
                                    all_jobs.append(job_copy)
                                    
            except Exception as e:
                print(f"Error fetching jobs: {e}")
    
        return all_jobs

    def _check_title_keywords(self, job_title: str) -> bool:
        """Check if job title matches any required title keywords"""
        if not self.title_keywords:
            return True
        
        title_lower = job_title.lower()
        return any(keyword.lower() in title_lower for keyword in self.title_keywords)

    def is_job_seen(self, job_url: str) -> bool:
        """Check if we've seen this job before"""
        return bool(self.redis.sismember('seen_jobs', job_url))

    def mark_job_seen(self, job_url: str) -> None:
        """Mark a job as seen"""
        self.redis.sadd('seen_jobs', job_url)

    def send_email_alert(self, new_jobs: List[Job]) -> bool:
        """Send email alert for new jobs"""
        email_body: str = "New job postings found:\n\n"
        
        for job in new_jobs:
            email_body += f"Title: {job['title']}\n"
            email_body += f"Company: {job['company']}\n"
            email_body += f"Location: {job['location']['name']}\n"
            email_body += f"Apply here: {job['absolute_url']}\n"
            
            if job.get('content'):
                email_body += f"\nDescription: {job['content'][:200]}...\n"
            email_body += "\n" + "-"*50 + "\n\n"

        if not self.config.send_emails:
            print("\nEmail would have contained:")
            print(email_body)
            return True  # Return True in test mode so jobs are marked as seen

        msg: MIMEText = MIMEText(email_body)
        msg['Subject'] = f"New Jobs Alert - {len(new_jobs)} new {"position" if len(new_jobs) == 1 else "positions"} found"
        msg['From'] = self.email_address
        msg['To'] = self.email_address

        sent_email = False

        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.email_address, self.email_password)
                server.send_message(msg)
            print(f"Alert sent at {datetime.now()}")
            sent_email = True
        except Exception as e:
            print(f"Failed to send email: {e}")

        return sent_email

    def check_jobs(self) -> None:
        """Main function to check for new jobs"""
        try:
            print(f"Starting job scan at {datetime.now()}")
            all_jobs: List[Job] = self.fetch_jobs()
            new_jobs: List[Job] = []

            for job in all_jobs:
                # Check if the title matches keywords
                if not self._check_title_keywords(job['title']):
                    continue
                    
                job_url: str = job['absolute_url']
                
                # Check if we haven't seen this job before
                if not self.is_job_seen(job_url):
                    new_jobs.append(job)
            
            if new_jobs:
                # Only mark the jobs as seen if the email sent successfully
                if self.send_email_alert(new_jobs):
                    for job in new_jobs:
                        self.mark_job_seen(job['absolute_url'])

                print(f"Found and reported {len(new_jobs)} new jobs")
            else:
                print("No new matching jobs found")
                
        except Exception as e:
            print(f"Error during job scan: {e}")

if __name__ == "__main__":
    load_dotenv()
    
    parser = argparse.ArgumentParser(description='Job Board Scanner')
    parser.add_argument('--test', action='store_true', 
                      help='Run in test mode (no Redis, no emails)')
    parser.add_argument('--no-email', action='store_true',
                      help='Skip sending emails but use Redis')
    parser.add_argument('--title-keywords', type=str, nargs='+', default=['engineer'],
                      help='Keywords to filter job titles (case insensitive)')
    args = parser.parse_args()

    # Configure based on arguments
    config = ScannerConfig(
        use_redis=not args.test,
        send_emails=not args.test and not args.no_email,
        title_keywords=args.title_keywords
    )
    
    scanner = GreenhouseJobScanner(config)
    scanner.check_jobs()