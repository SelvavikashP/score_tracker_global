import requests
import re
import json
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
}

def fetch_codeforces_data(handle):
    """
    Fetches detailed Codeforces data including last contest performance.
    """
    try:
        # User info
        info_url = f"https://codeforces.com/api/user.info?handles={handle}"
        info_resp = requests.get(info_url, headers=DEFAULT_HEADERS, timeout=10).json()
        
        # Rating history
        rating_url = f"https://codeforces.com/api/user.rating?handle={handle}"
        rating_resp = requests.get(rating_url, headers=DEFAULT_HEADERS, timeout=10).json()

        # Status - fetch more to get a better count of unique solved problems
        status_url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=1000"
        status_resp = requests.get(status_url, headers=DEFAULT_HEADERS, timeout=10).json()

        if info_resp["status"] == "OK":
            user_info = info_resp["result"][0]
            rating_history = rating_resp.get("result", [])
            total_contests = len(rating_history)
            
            # Last contest rank
            last_contest_rank = rating_history[-1].get("rank", 0) if rating_history else 0
            
            # Total unique problems solved (from the last 1000 submissions)
            total_solved = 0
            if status_resp.get("status") == "OK":
                solved_problems = set()
                for sub in status_resp["result"]:
                    if sub.get("verdict") == "OK":
                        prob = sub['problem']
                        p_id = f"{prob.get('contestId', '')}{prob.get('index', '')}"
                        solved_problems.add(p_id)
                total_solved = len(solved_problems)

            return {
                "rating": user_info.get("rating", 0),
                "rank": user_info.get("rank", "Unrated"),
                "global_rank": last_contest_rank,
                "country_rank": 0,
                "recent_problems": total_solved, # Switched to Total Solved for consistency
                "total_contests": total_contests
            }
    except Exception as e:
        print(f"Error fetching Codeforces data for {handle}: {e}")
    return None

def fetch_leetcode_data(handle):
    """
    Fetches LeetCode data via GraphQL with enhanced headers to avoid 402/403.
    """
    url = "https://leetcode.com/graphql"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Referer': f'https://leetcode.com/{handle}/',
        'Origin': 'https://leetcode.com',
        'Connection': 'keep-alive',
        'Content-Type': 'application/json'
    }
    
    query = {
        "query": """
        query userContestRankingInfo($username: String!) {
          userContestRanking(username: $username) {
            attendedContestsCount
            rating
            globalRanking
          }
          recentSubmissionList(username: $username, limit: 20) {
            timestamp
            statusDisplay
          }
        }
        """,
        "variables": {"username": handle}
    }
    try:
        response = requests.post(url, json=query, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
            
        data = response.json()
        if "data" in data and data["data"].get("userContestRanking"):
            ranking = data["data"]["userContestRanking"]
            
            # Fetch Total Solved from matchedUser
            total_solved = 0
            mu_query = {
                "query": """
                query userProblemsSolved($username: String!) {
                  matchedUser(username: $username) {
                    submitStats {
                      acSubmissionNum {
                        difficulty
                        count
                      }
                    }
                  }
                }
                """,
                "variables": {"username": handle}
            }
            mu_resp = requests.post(url, json=mu_query, headers=headers, timeout=10)
            if mu_resp.status_code == 200:
                mu_data = mu_resp.json()
                stats = mu_data.get("data", {}).get("matchedUser", {}).get("submitStats", {}).get("acSubmissionNum", [])
                for s in stats:
                    if s.get("difficulty") == "All":
                        total_solved = s.get("count", 0)

            return {
                "rating": int(ranking.get("rating", 0)),
                "rank": "LeetCoder",
                "global_rank": ranking.get("globalRanking", 0),
                "country_rank": 0,
                "recent_problems": total_solved,
                "total_contests": ranking.get("attendedContestsCount", 0)
            }
        else:
            # Fallback for users with no contest history
            mu_query = {
                "query": """
                query userProblemsSolved($username: String!) {
                  matchedUser(username: $username) {
                    submitStats {
                      acSubmissionNum {
                        difficulty
                        count
                      }
                    }
                  }
                }
                """,
                "variables": {"username": handle}
            }
            mu_resp = requests.post(url, json=mu_query, headers=headers, timeout=10)
            if mu_resp.status_code == 200:
                mu_data = mu_resp.json()
                stats = mu_data.get("data", {}).get("matchedUser", {}).get("submitStats", {}).get("acSubmissionNum", [])
                total_solved = 0
                for s in stats:
                    if s.get("difficulty") == "All":
                        total_solved = s.get("count", 0)
                
                return {
                    "rating": 0, "rank": "LeetCoder", "global_rank": 0,
                    "country_rank": 0, "recent_problems": total_solved, "total_contests": 0
                }
    except Exception as e:
        print(f"Error fetching LeetCode data for {handle}: {e}")
    return None

def fetch_codechef_data(handle):
    """
    Fetches CodeChef data using a more reliable unofficial API wrapper.
    Then scrapes the profile to fill in missing contest/solved data.
    """
    result = {
        "rating": 0, "rank": "Unrated", "global_rank": 0,
        "country_rank": 0, "recent_problems": 0, "total_contests": 0
    }
    
    # Try the unofficial API first for reliable rating/ranks
    api_url = f"https://codechef-api.vercel.app/{handle}"
    try:
        response = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                result["rating"] = data.get("currentRating", 0)
                result["rank"] = data.get("stars", "Unrated")
                result["global_rank"] = data.get("globalRank", 0)
                result["country_rank"] = data.get("countryRank", 0)
    except: pass

    # Scrape to get actual contests and solved problems (API doesn't have it)
    url = f"https://www.codechef.com/users/{handle}"
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if response.status_code == 200 and "This user is blocked" not in response.text:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Fallback for ratings/ranks if API failed
            if result["rating"] == 0:
                rating_div = soup.find('div', class_='rating-number')
                result["rating"] = int(rating_div.text) if rating_div else 0
                
                stars_span = soup.find('span', class_='rating') or soup.find('div', class_='rating-star') or soup.find('span', class_='rating-star')
                result["rank"] = stars_span.text.strip() if stars_span else "Unrated"
                
                rank_list = soup.find('div', class_='rating-ranks')
                if rank_list:
                    ranks = rank_list.find_all('strong')
                    if len(ranks) >= 2:
                        result["global_rank"] = int(ranks[0].text) if ranks[0].text.isdigit() else 0
                        result["country_rank"] = int(ranks[1].text) if ranks[1].text.isdigit() else 0

            # Total contests
            participated_match = re.search(r'No\. of Contests Participated.*?(\d+)', response.text)
            if participated_match:
                result["total_contests"] = int(participated_match.group(1))
            else:
                contest_matches = re.findall(r'(\d+)\s+Contests', response.text)
                if contest_matches:
                    result["total_contests"] = int(contest_matches[0])
                
            # Total solved problems
            solved_heading = soup.find(lambda tag: tag.name == "h3" and "Total Problems Solved" in tag.text)
            if solved_heading:
                count_match = re.search(r'(\d+)', solved_heading.text)
                if count_match:
                    result["recent_problems"] = int(count_match.group(1))
            
            if result["recent_problems"] == 0:
                solved_section = soup.find('section', class_='problems-solved')
                if solved_section:
                    try:
                        h3_text = solved_section.find('h3').text
                        count_match = re.search(r'\((\d+)\)', h3_text)
                        if count_match:
                            result["recent_problems"] = int(count_match.group(1))
                    except: pass
                    
    except Exception as e:
        print(f"Error scraping CodeChef data for {handle}: {e}")
        
    return result

def fetch_hackerrank_data(handle):
    """
    Fetches HackerRank data.
    """
    url = f"https://www.hackerrank.com/rest/contests/master/users/{handle}/profile"
    headers = DEFAULT_HEADERS.copy()
    headers.update({'Accept': 'application/json'})
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json().get('model', {})
            # HackerRank doesn't expose a single "contest count" easily via this API,
            # but we can show solved challenges as recent problems.
            return {
                "rating": 0,
                "rank": "Hacker",
                "global_rank": 0,
                "country_rank": 0,
                "recent_problems": data.get('solved_challenges_count', 0),
                "total_contests": 0
            }
        else:
            return {
                "rating": 0,
                "rank": "Hacker",
                "global_rank": 0,
                "country_rank": 0,
                "recent_problems": 0,
                "total_contests": 0
            }
    except Exception as e:
        print(f"Error fetching HackerRank data for {handle}: {e}")
    return None

def fetch_atcoder_data(handle):
    """
    Fetches AtCoder data from kenkoooo wrapper and scrapes profile for solved count.
    """
    url = f"https://atcoder.jp/users/{handle}/history/json"
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        history = response.json()
        if response.status_code == 200 and history:
            latest = history[-1]
            
            # Scrape profile for total solved problems
            recent_problems = 0
            try:
                profile_url = f"https://atcoder.jp/users/{handle}"
                p_resp = requests.get(profile_url, headers=DEFAULT_HEADERS, timeout=10)
                if p_resp.status_code == 200:
                    soup = BeautifulSoup(p_resp.text, 'html.parser')
                    # Look for Tasks Solved
                    solved_tag = soup.find(string=re.compile("Tasks Solved"))
                    if solved_tag:
                        td_val = solved_tag.find_next('td')
                        if td_val:
                            recent_problems = int(td_val.text.split()[0])
            except: pass

            return {
                "rating": latest.get("NewRating", 0),
                "rank": "AtCoder",
                "global_rank": latest.get("Place", 0),
                "country_rank": 0,
                "recent_problems": recent_problems,
                "total_contests": len(history)
            }
    except Exception as e:
        print(f"Error fetching AtCoder data for {handle}: {e}")
    return None

def extract_handle_from_url(url, platform):
    """
    Extracts the handle from a profile URL.
    Resilient to trailing slashes and common URL prefixes.
    """
    url = url.strip().strip('/')
    if not url: return None
    
    # Platform specific patterns
    patterns = {
        "codeforces": r"codeforces\.com/profile/([^/?#]+)",
        "leetcode": r"leetcode\.com/(?:u/)?([^/?#]+)",
        "codechef": r"codechef\.com/users/([^/?#]+)",
        "atcoder": r"atcoder\.jp/users/([^/?#]+)",
        "hackerrank": r"hackerrank\.com/profile/([^/?#]+)"
    }
    
    p = platform.lower()
    pattern = patterns.get(p)
    if pattern:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
            
    # Fallback: if user just provided the handle (no dots, no slashes)
    if '/' not in url and '.' not in url:
        return url
        
    return None

def fetch_user_data(url, platform):
    """
    General function to fetch user data based on platform.
    """
    handle = extract_handle_from_url(url, platform)
    if not handle:
        print(f"Error: Could not extract handle for {platform} from {url}")
        return None
    
    print(f"Fetching data for {platform} handle: {handle}")
    p = platform.lower()
    try:
        if p == "codeforces":
            return fetch_codeforces_data(handle)
        elif p == "leetcode":
            return fetch_leetcode_data(handle)
        elif p == "codechef":
            return fetch_codechef_data(handle)
        elif p == "atcoder":
            return fetch_atcoder_data(handle)
        elif p == "hackerrank":
            return fetch_hackerrank_data(handle)
    except Exception as e:
        print(f"Critical error fetching {platform} data: {e}")
    
    return None
