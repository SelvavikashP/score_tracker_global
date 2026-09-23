from datetime import datetime, timezone, timedelta
from models_mongo import (
    User, PlatformProfile, PerformanceSnapshot, ClassroomMembership,
    get_profiles_col, get_snapshots_col, to_object_id
)
from api_utils import fetch_user_data
from scoring import compute_profile_score
from config import Config

def sync_single_platform_profile(profile, force=False):
    """
    Synchronizes a single PlatformProfile with external APIs in a fault-tolerant manner.
    Updates the profile in MongoDB and creates/updates today's PerformanceSnapshot.
    """
    now = datetime.now(timezone.utc)
    today_str = now.strftime('%Y-%m-%d')
    
    # Check cache threshold if not forced
    if not force and profile.last_synced_at:
        last_sync = profile.last_synced_at
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=timezone.utc)
        elapsed = now - last_sync
        if elapsed < timedelta(minutes=Config.SYNC_CACHE_MINUTES) and profile.sync_status == 'success':
            return {
                "success": True,
                "cached": True,
                "platform": profile.platform,
                "handle": profile.handle,
                "message": "Loaded from recent cache"
            }

    try:
        data = fetch_user_data(profile.handle, profile.platform)
        if not data:
            profile.sync_status = 'failed'
            profile.sync_error = 'Platform returned empty or invalid response'
            profile.last_synced_at = now
            get_profiles_col().update_one(
                {'_id': to_object_id(profile.id)},
                {'$set': {
                    'sync_status': 'failed',
                    'sync_error': profile.sync_error,
                    'last_synced_at': now
                }}
            )
            return {
                "success": False,
                "platform": profile.platform,
                "handle": profile.handle,
                "error": profile.sync_error
            }

        # Update current profile metrics
        profile.rating = int(data.get('rating', 0) or 0)
        profile.rank = str(data.get('rank', 'Unrated') or 'Unrated')
        profile.global_rank = int(data.get('global_rank', 0) or 0)
        profile.country_rank = int(data.get('country_rank', 0) or 0)
        profile.recent_problems = int(data.get('recent_problems', 0) or 0)
        profile.total_contests = int(data.get('total_contests', 0) or 0)
        profile.last_synced_at = now
        profile.sync_status = 'success'
        profile.sync_error = None

        # Fetch latest previous snapshot before today to compute deltas
        prev_doc = get_snapshots_col().find_one(
            {
                'user_id': str(profile.user_id),
                'platform': profile.platform,
                'snapshot_date': {'$lt': today_str}
            },
            sort=[('snapshot_date', -1)]
        )
        prev_snapshot = PerformanceSnapshot(prev_doc) if prev_doc else None

        rating_delta = 0
        problems_delta = 0
        contests_delta = 0

        if prev_snapshot:
            rating_delta = profile.rating - prev_snapshot.rating
            problems_delta = max(0, profile.recent_problems - prev_snapshot.problems_solved)
            contests_delta = max(0, profile.total_contests - prev_snapshot.contests)

        calc_score = compute_profile_score(profile, previous_snapshot=prev_snapshot)

        # Create or update today's PerformanceSnapshot
        PerformanceSnapshot.create({
            'user_id': str(profile.user_id),
            'platform': profile.platform,
            'snapshot_date': today_str,
            'rating': profile.rating,
            'rating_delta': rating_delta,
            'rank': profile.rank,
            'global_rank': profile.global_rank,
            'country_rank': profile.country_rank,
            'problems_solved': profile.recent_problems,
            'problems_solved_delta': problems_delta,
            'contests': profile.total_contests,
            'contests_delta': contests_delta,
            'calculated_score': calc_score,
            'created_at': now
        })

        # Update profile in MongoDB
        get_profiles_col().update_one(
            {'_id': to_object_id(profile.id)},
            {'$set': {
                'rating': profile.rating,
                'rank': profile.rank,
                'global_rank': profile.global_rank,
                'country_rank': profile.country_rank,
                'recent_problems': profile.recent_problems,
                'total_contests': profile.total_contests,
                'last_synced_at': now,
                'sync_status': 'success',
                'sync_error': None
            }}
        )

        return {
            "success": True,
            "platform": profile.platform,
            "handle": profile.handle,
            "rating": profile.rating,
            "solved": profile.recent_problems,
            "score": calc_score
        }
    except Exception as e:
        profile.sync_status = 'failed'
        profile.sync_error = str(e)[:250]
        profile.last_synced_at = now
        try:
            get_profiles_col().update_one(
                {'_id': to_object_id(profile.id)},
                {'$set': {
                    'sync_status': 'failed',
                    'sync_error': profile.sync_error,
                    'last_synced_at': now
                }}
            )
        except Exception:
            pass
        return {
            "success": False,
            "platform": profile.platform,
            "handle": profile.handle,
            "error": str(e)
        }

def sync_student_profiles(student_id, force=False):
    """
    Synchronizes all platform profiles for a given student.
    Guarantees fault-isolation across platforms.
    """
    student = User.find_by_id(student_id)
    if not student or not student.is_active:
        return {"success": False, "error": "Student not found or inactive"}

    profiles = PlatformProfile.find_by_user_id(student_id)
    results = []
    success_count = 0

    for p in profiles:
        res = sync_single_platform_profile(p, force=force)
        results.append(res)
        if res.get("success"):
            success_count += 1

    return {
        "success": True,
        "student_id": str(student_id),
        "total": len(profiles),
        "synced": success_count,
        "details": results
    }

def sync_classroom_profiles(classroom_id, force=False):
    """
    Synchronizes platform profiles for all active students in a classroom.
    """
    memberships = ClassroomMembership.find_by_classroom_id(
        classroom_id=classroom_id,
        status='active'
    )

    student_ids = [m.student_id for m in memberships]
    total_profiles = 0
    synced_profiles = 0

    for s_id in student_ids:
        res = sync_student_profiles(s_id, force=force)
        total_profiles += res.get("total", 0)
        synced_profiles += res.get("synced", 0)

    return {
        "classroom_id": str(classroom_id),
        "total_students": len(student_ids),
        "total_profiles": total_profiles,
        "synced_profiles": synced_profiles
    }

def sync_all_portal_profiles(force=False):
    """
    Portal-wide synchronization function used by background scheduler or Admin.
    """
    active_students = User.find_all({'role': 'student', 'is_active': True})
    synced_students = 0
    total_synced_profiles = 0

    for s in active_students:
        res = sync_student_profiles(s.id, force=force)
        if res.get("synced", 0) > 0:
            synced_students += 1
            total_synced_profiles += res.get("synced", 0)

    return {
        "active_students": len(active_students),
        "synced_students": synced_students,
        "total_profiles_synced": total_synced_profiles,
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
