import requests
import re
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}

def extract_handle_and_canonical_url(input_str, platform):
    """
    Extracts the handle and returns both (handle, canonical_profile_url).
    Supports raw handles and various URL formats.
    """
    if not input_str:
        return None, None
    raw = input_str.strip().strip('/')
    if not raw:
        return None, None
    p = platform.strip().lower()

    # Regex patterns for platforms
    patterns = {
        "codeforces": r"(?:https?://)?(?:www\.)?codeforces\.com/profile/([^/?#\s]+)",
        "leetcode": r"(?:https?://)?(?:www\.)?leetcode\.com/(?:u/)?([^/?#\s]+)",
        "codechef": r"(?:https?://)?(?:www\.)?codechef\.com/users/([^/?#\s]+)",
        "atcoder": r"(?:https?://)?(?:www\.)?atcoder\.jp/users/([^/?#\s]+)",
        "hackerrank": r"(?:https?://)?(?:www\.)?hackerrank\.com/(?:profile/)?([^/?#\s]+)"
    }

    handle = None
    pattern = patterns.get(p)
    if pattern:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            handle = match.group(1).strip()

    # Fallback: if user provided just the handle (no slash, no common domain suffix)
    if not handle and '/' not in raw and not any(raw.lower().endswith(dom) for dom in ['.com', '.jp', '.org', '.net', '.in']):
        handle = raw

    if not handle:
        return None, None

    # Construct canonical profile URL
    url_templates = {
        "codeforces": f"https://codeforces.com/profile/{handle}",
        "leetcode": f"https://leetcode.com/u/{handle}/",
        "codechef": f"https://www.codechef.com/users/{handle}",
        "atcoder": f"https://atcoder.jp/users/{handle}",
        "hackerrank": f"https://www.hackerrank.com/profile/{handle}"
    }
    canonical_url = url_templates.get(p, f"https://{p}.com/{handle}")
    return handle, canonical_url

def extract_handle_from_url(url, platform):
    handle, _ = extract_handle_and_canonical_url(url, platform)
    return handle

def fetch_codeforces_data(handle):
    """
    Fetches detailed Codeforces data.
    """
    try:
        # User info
        info_url = f"https://codeforces.com/api/user.info?handles={handle}"
        info_resp = requests.get(info_url, headers=DEFAULT_HEADERS, timeout=10)
        if info_resp.status_code != 200:
            return None
        info_data = info_resp.json()
        if info_data.get("status") != "OK" or not info_data.get("result"):
            return None

        user_info = info_data["result"][0]

        # Rating history
        rating_url = f"https://codeforces.com/api/user.rating?handle={handle}"
        rating_history = []
        try:
            rating_resp = requests.get(rating_url, headers=DEFAULT_HEADERS, timeout=10)
            if rating_resp.status_code == 200:
                r_json = rating_resp.json()
                if r_json.get("status") == "OK":
                    rating_history = r_json.get("result", [])
        except Exception:
            pass

        total_contests = len(rating_history)
        last_contest_rank = rating_history[-1].get("rank", 0) if rating_history else 0

        # Solved problems count from submissions
        total_solved = 0
        try:
            status_url = f"https://codeforces.com/api/user.status?handle={handle}&from=1&count=1000"
            status_resp = requests.get(status_url, headers=DEFAULT_HEADERS, timeout=10)
            if status_resp.status_code == 200:
                s_json = status_resp.json()
                if s_json.get("status") == "OK":
                    solved_problems = set()
                    for sub in s_json.get("result", []):
                        if sub.get("verdict") == "OK":
                            prob = sub.get('problem', {})
                            p_id = f"{prob.get('contestId', '')}{prob.get('index', '')}"
                            if p_id:
                                solved_problems.add(p_id)
                    total_solved = len(solved_problems)
        except Exception:
            pass

        return {
            "rating": user_info.get("rating", 0),
            "rank": user_info.get("rank", "Unrated").title() if user_info.get("rank") else "Unrated",
            "global_rank": last_contest_rank,
            "country_rank": 0,
            "recent_problems": total_solved,
            "total_contests": total_contests
        }
    except Exception as e:
        print(f"Error fetching Codeforces data for {handle}: {e}")
        return None

def fetch_leetcode_data(handle):
    """
    Fetches LeetCode statistics via GraphQL endpoint.
    """
    url = "https://leetcode.com/graphql"
    headers = {
        'User-Agent': DEFAULT_HEADERS['User-Agent'],
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Referer': 'https://leetcode.com',
        'Origin': 'https://leetcode.com'
    }

    query = """
    query getUserProfile($username: String!) {
      matchedUser(username: $username) {
        username
        submitStatsGlobal {
          acSubmissionNum {
            difficulty
            count
          }
        }
      }
      userContestRanking(username: $username) {
        attendedContestsCount
        rating
        globalRanking
        badge {
          name
        }
      }
    }
    """

    try:
        response = requests.post(url, json={"query": query, "variables": {"username": handle}}, headers=headers, timeout=10)
        if response.status_code != 200:
            return None

        data = response.json().get("data") or {}
        matched_user = data.get("matchedUser")
        if not matched_user:
            return None

        # Calculate solved count
        stats = matched_user.get("submitStatsGlobal", {}).get("acSubmissionNum", [])
        total_solved = 0
        for s in stats:
            if s.get("difficulty") == "All":
                total_solved = s.get("count", 0)
                break

        contest = data.get("userContestRanking")
        rating = 0
        rank_name = "LeetCoder"
        global_rank = 0
        total_contests = 0

        if contest:
            rating = int(round(contest.get("rating", 0)))
            global_rank = contest.get("globalRanking", 0)
            total_contests = contest.get("attendedContestsCount", 0)
            badge = contest.get("badge")
            if badge and badge.get("name"):
                rank_name = badge.get("name")
            elif rating >= 2200:
                rank_name = "Guardian"
            elif rating >= 1850:
                rank_name = "Knight"

        return {
            "rating": rating,
            "rank": rank_name,
            "global_rank": global_rank,
            "country_rank": 0,
            "recent_problems": total_solved,
            "total_contests": total_contests
        }
    except Exception as e:
        print(f"Error fetching LeetCode data for {handle}: {e}")
        return None

def fetch_codechef_data(handle):
    """
    Fetches CodeChef data by parsing user profile page directly.
    """
    url = f"https://www.codechef.com/users/{handle}"
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)
        if response.status_code != 200:
            return None

        # Check if user is blocked or invalid
        if "This user is blocked" in response.text:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # Verify profile exists
        rating_div = soup.find('div', class_='rating-number')
        user_container = (
            soup.find('div', class_='user-details-container') or
            soup.find('section', class_='user-details') or
            soup.find('div', class_='user-profile-head') or
            soup.find('div', class_='rating-ranks')
        )
        if not rating_div and not user_container:
            return None

        rating = 0
        if rating_div and rating_div.text.strip().isdigit():
            rating = int(rating_div.text.strip())

        # Stars / Rank
        stars_span = (
            soup.find('div', class_='rating-star') or
            soup.find('span', class_='rating-star') or
            soup.find('span', class_='rating')
        )
        rank_str = stars_span.text.strip() if stars_span else ("Unrated" if rating == 0 else f"{rating} pts")

        # Ranks
        global_rank = 0
        country_rank = 0
        rank_list = soup.find('div', class_='rating-ranks')
        if rank_list:
            ranks = rank_list.find_all('strong')
            if len(ranks) >= 1 and ranks[0].text.strip().isdigit():
                global_rank = int(ranks[0].text.strip())
            if len(ranks) >= 2 and ranks[1].text.strip().isdigit():
                country_rank = int(ranks[1].text.strip())

        # Total contests
        total_contests = 0
        participated_match = re.search(r'No\. of Contests Participated.*?(\d+)', response.text)
        if participated_match:
            total_contests = int(participated_match.group(1))
        else:
            contest_matches = re.findall(r'(\d+)\s+Contests', response.text)
            if contest_matches:
                total_contests = int(contest_matches[0])

        # Total solved problems
        recent_problems = 0
        solved_heading = soup.find(lambda tag: tag.name in ["h3", "h4", "h5"] and "Total Problems Solved" in tag.text)
        if solved_heading:
            count_match = re.search(r'(\d+)', solved_heading.text)
            if count_match:
                recent_problems = int(count_match.group(1))

        if recent_problems == 0:
            solved_section = soup.find('section', class_='problems-solved')
            if solved_section:
                try:
                    h3_text = solved_section.find('h3').text
                    count_match = re.search(r'\((\d+)\)', h3_text)
                    if count_match:
                        recent_problems = int(count_match.group(1))
                except Exception:
                    pass

        return {
            "rating": rating,
            "rank": rank_str,
            "global_rank": global_rank,
            "country_rank": country_rank,
            "recent_problems": recent_problems,
            "total_contests": total_contests
        }
    except Exception as e:
        print(f"Error fetching CodeChef data for {handle}: {e}")
        return None

def fetch_atcoder_data(handle):
    """
    Fetches AtCoder data using history API and Kenkoooo stats API.
    """
    try:
        # Check if user profile exists
        profile_url = f"https://atcoder.jp/users/{handle}"
        p_resp = requests.get(profile_url, headers=DEFAULT_HEADERS, timeout=10)
        if p_resp.status_code != 200:
            return None

        # Fetch contest history
        url = f"https://atcoder.jp/users/{handle}/history/json"
        rating = 0
        global_rank = 0
        total_contests = 0
        try:
            h_resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
            if h_resp.status_code == 200:
                history = h_resp.json()
                if isinstance(history, list) and history:
                    latest = history[-1]
                    rating = latest.get("NewRating", 0)
                    global_rank = latest.get("Place", 0)
                    total_contests = len(history)
        except Exception:
            pass

        # Fetch solved problems count via Kenkoooo API
        recent_problems = 0
        try:
            ac_url = f"https://kenkoooo.com/atcoder/atcoder-api/v3/user/ac_rank?user={handle}"
            ac_resp = requests.get(ac_url, headers=DEFAULT_HEADERS, timeout=10)
            if ac_resp.status_code == 200:
                ac_data = ac_resp.json()
                recent_problems = ac_data.get("count", 0)
        except Exception:
            pass

        # Rating color / rank title for AtCoder
        def atcoder_color_rank(r):
            if r >= 2800: return "Red"
            if r >= 2400: return "Orange"
            if r >= 2000: return "Yellow"
            if r >= 1600: return "Blue"
            if r >= 1200: return "Cyan"
            if r >= 800:  return "Green"
            if r >= 400:  return "Brown"
            if r > 0:    return "Gray"
            return "Unrated"

        return {
            "rating": rating,
            "rank": atcoder_color_rank(rating),
            "global_rank": global_rank,
            "country_rank": 0,
            "recent_problems": recent_problems,
            "total_contests": total_contests
        }
    except Exception as e:
        print(f"Error fetching AtCoder data for {handle}: {e}")
        return None

def fetch_hackerrank_data(handle):
    """
    Fetches HackerRank profile statistics and badges.
    """
    url = f"https://www.hackerrank.com/rest/hackers/{handle}"
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        model = data.get('model')
        if not model or model.get('deleted'):
            return None

        # Fetch badges to aggregate solved challenges
        recent_problems = 0
        try:
            b_resp = requests.get(f"https://www.hackerrank.com/rest/hackers/{handle}/badges", headers=DEFAULT_HEADERS, timeout=8)
            if b_resp.status_code == 200:
                badges = b_resp.json().get('models', [])
                recent_problems = sum(b.get('solved', 0) for b in badges)
        except Exception:
            pass

        # Fetch scores
        rating = 0
        global_rank = 0
        try:
            s_resp = requests.get(f"https://www.hackerrank.com/rest/hackers/{handle}/scores_elo", headers=DEFAULT_HEADERS, timeout=8)
            if s_resp.status_code == 200:
                scores = s_resp.json()
                if isinstance(scores, list):
                    for s in scores:
                        if s.get('slug') == 'algorithms':
                            rating = int(s.get('practice', {}).get('score', 0))
                            r_val = s.get('practice', {}).get('rank', 0)
                            if isinstance(r_val, int):
                                global_rank = r_val
                            break
        except Exception:
            pass

        return {
            "rating": rating,
            "rank": "Hacker",
            "global_rank": global_rank,
            "country_rank": 0,
            "recent_problems": recent_problems,
            "total_contests": 0
        }
    except Exception as e:
        print(f"Error fetching HackerRank data for {handle}: {e}")
        return None

def fetch_user_data(url_or_handle, platform):
    """
    General function to fetch user data based on platform and URL or handle.
    """
    handle, canonical_url = extract_handle_and_canonical_url(url_or_handle, platform)
    if not handle:
        print(f"Error: Could not extract handle for {platform} from {url_or_handle}")
        return None

    p = platform.strip().lower()
    data = None
    try:
        if p == "codeforces":
            data = fetch_codeforces_data(handle)
        elif p == "leetcode":
            data = fetch_leetcode_data(handle)
        elif p == "codechef":
            data = fetch_codechef_data(handle)
        elif p == "atcoder":
            data = fetch_atcoder_data(handle)
        elif p == "hackerrank":
            data = fetch_hackerrank_data(handle)
    except Exception as e:
        print(f"Critical error fetching {platform} data: {e}")

    if data:
        data["canonical_url"] = canonical_url
        data["handle"] = handle
    return data
