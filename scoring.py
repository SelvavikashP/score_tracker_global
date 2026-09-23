"""
Configurable Scoring & Leaderboard Calculation Engine.

DOCUMENTATION:
The scoring engine computes performance scores for students across coding platforms
by normalizing platform metrics, factoring in current standing, and awarding bonuses
for daily progress (deltas).

Formulas:
1. Platform Component Score:
   Score = (Rating * Rating_Weight * Platform_Multiplier) 
         + (Problems_Solved * Problem_Weight * Platform_Multiplier)
         + (Contests * Contest_Weight * Platform_Multiplier)
         + (Rating_Delta * Rating_Delta_Weight)
         + (Problems_Delta * Problem_Delta_Weight)
         + (Contests_Delta * Contest_Delta_Weight)

2. Total Student Score:
   Sum of Platform Component Scores across all connected active platform profiles.
"""

DEFAULT_SCORING_WEIGHTS = {
    # Base Metric Multipliers
    "rating_weight": 1.0,
    "problem_weight": 12.0,
    "contest_weight": 25.0,
    
    # Progress Delta Multipliers (Reward active improvement between snapshots)
    "rating_delta_weight": 2.5,
    "problem_delta_weight": 20.0,
    "contest_delta_weight": 35.0,
    
    # Platform Normalization Factors (Accounts for platform grading scales)
    "platform_multipliers": {
        "Codeforces": 1.0,
        "LeetCode": 1.0,
        "CodeChef": 0.95,
        "AtCoder": 1.05,
        "HackerRank": 0.85
    }
}

def calculate_snapshot_score(rating, problems_solved, contests,
                             rating_delta=0, problems_delta=0, contests_delta=0,
                             platform="Codeforces", custom_weights=None):
    """
    Computes an exact, normalized score for a single platform snapshot.
    """
    w = custom_weights or DEFAULT_SCORING_WEIGHTS
    plat_mult = w.get("platform_multipliers", {}).get(platform, 1.0)
    
    base_rating = max(0, rating) * w.get("rating_weight", 1.0) * plat_mult
    base_problems = max(0, problems_solved) * w.get("problem_weight", 12.0) * plat_mult
    base_contests = max(0, contests) * w.get("contest_weight", 25.0) * plat_mult
    
    delta_rating = rating_delta * w.get("rating_delta_weight", 2.5)
    delta_problems = max(0, problems_delta) * w.get("problem_delta_weight", 20.0)
    delta_contests = max(0, contests_delta) * w.get("contest_delta_weight", 35.0)
    
    total = base_rating + base_problems + base_contests + delta_rating + delta_problems + delta_contests
    return round(total, 1)

def compute_profile_score(profile, previous_snapshot=None, custom_weights=None):
    """
    Calculates the score for an active PlatformProfile model instance,
    incorporating deltas from a previous snapshot if available.
    """
    r_delta = 0
    p_delta = 0
    c_delta = 0
    
    if previous_snapshot:
        r_delta = profile.rating - previous_snapshot.rating
        p_delta = max(0, profile.recent_problems - previous_snapshot.problems_solved)
        c_delta = max(0, profile.total_contests - previous_snapshot.contests)
        
    return calculate_snapshot_score(
        rating=profile.rating,
        problems_solved=profile.recent_problems,
        contests=profile.total_contests,
        rating_delta=r_delta,
        problems_delta=p_delta,
        contests_delta=c_delta,
        platform=profile.platform,
        custom_weights=custom_weights
    )
