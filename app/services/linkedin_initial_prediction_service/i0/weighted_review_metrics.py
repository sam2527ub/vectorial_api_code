import numpy as np

EPSILON = 1e-10


def _kl_divergence_base2(p: np.ndarray, q: np.ndarray) -> float:
    """KL(P||Q) with log base 2 (same as ``scipy.stats.entropy(p, q, base=2)``)."""
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    mask = p > 0
    if not np.any(mask):
        return 0.0
    return float(np.sum(p[mask] * np.log2(p[mask] / q[mask])))


def _cosine_distance(u, v) -> float:
    """Cosine distance (same as ``scipy.spatial.distance.cosine``)."""
    a = np.asarray(u, dtype=np.float64)
    b = np.asarray(v, dtype=np.float64)
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(1.0 - np.dot(a, b) / (na * nb))

def calculate_jsd(predicted_dist: dict, actual_dist: dict, all_themes: list = None) -> float:
    """
    Calculate Jensen-Shannon Divergence between two theme probability distributions.
    
    JSD(P||Q) = 0.5 * KL(P||M) + 0.5 * KL(Q||M)
    where M = 0.5 * (P + Q)
    
    Args:
        predicted_dist: dict with theme names as keys and probabilities as values
        actual_dist: dict of GT/predicted probabilities (preferred for JSD), or legacy list of
            theme names (treated as a uniform distribution over that list only).
        all_themes: Optional ordered support for the simplex (if None, uses union of dict keys).
    
    Returns:
        JSD value (float, range 0-1)
    """
    if not actual_dist:
        return 0.0
    if not predicted_dist or not isinstance(predicted_dist, dict):
        return 1.0
    
    # Handle actual_dist: can be dict (distribution) or list
    if isinstance(actual_dist, dict):
        actual_dict = actual_dist
    else:
        # Convert list to uniform distribution
        actual_dict = {str(t).strip(): 1.0 / len(actual_dist) if actual_dist else 0.0 for t in actual_dist}
    
    # Get all unique themes
    # If all_themes is None or empty list, use union of keys from both distributions
    # This ensures JSD can be calculated even when category lookup fails
    if all_themes is None or (isinstance(all_themes, list) and len(all_themes) == 0):
        all_themes = list(set(list(predicted_dist.keys()) + list(actual_dict.keys())))
    
    # After trying to get themes, if still empty, return 0.0
    # (This would only happen if both distributions are empty)
    if not all_themes:
        return 0.0
    
    # Create probability vectors
    P = np.array([predicted_dist.get(theme, 0.0) for theme in all_themes])
    Q = np.array([actual_dict.get(theme, 0.0) for theme in all_themes])
    
    # Normalize to ensure they sum to 1
    P_sum = P.sum()
    Q_sum = Q.sum()
    if P_sum > 0:
        P = P / P_sum
    else:
        P = np.ones_like(P) / len(P)  # Uniform if empty
    
    if Q_sum > 0:
        Q = Q / Q_sum
    else:
        Q = np.ones_like(Q) / len(Q)  # Uniform if empty
    
    # Add epsilon to avoid zeros
    P = P + EPSILON
    Q = Q + EPSILON
    
    # Re-normalize after adding epsilon
    P = P / P.sum()
    Q = Q / Q.sum()
    
    # Compute mixture distribution
    M = 0.5 * (P + Q)
    
    # Compute KL divergences
    kl_pm = _kl_divergence_base2(P, M)
    kl_qm = _kl_divergence_base2(Q, M)
    
    # JSD is the average of the two KL divergences
    jsd = 0.5 * kl_pm + 0.5 * kl_qm
    
    return float(jsd)

def calculate_text_delta(embedding1, embedding2):
    return _cosine_distance(embedding1, embedding2)

def calculate_theme_delta(predicted_themes, actual_themes):
    if not actual_themes: return 0.0
    if not predicted_themes or not isinstance(predicted_themes, dict): return 1.0
    
    cleaned_actual = [str(t).strip().lower() for t in actual_themes]
    k = len(cleaned_actual)
    n = max(3, k)
    
    # Sort predicted by score
    top_predicted = sorted(predicted_themes.keys(), key=lambda x: predicted_themes[x], reverse=True)[:n]
    cleaned_predicted = [str(t).strip().lower() for t in top_predicted]
    
    matches = sum(1 for t in cleaned_actual if t in cleaned_predicted)
    recall = matches / k if k > 0 else 0
    return 1.0 - recall

def calculate_theme_delta_logprobs(predicted_themes, actual_themes, all_themes=None):
    """
    Calculate theme delta for logprobs mode using Jensen-Shannon Divergence (JSD).
    Both predicted and actual are probability distributions.
    
    Args:
        predicted_themes: dict with theme names as keys and probabilities as values
        actual_themes: dict with theme names as keys and probabilities as values, OR list of theme names
        all_themes: Optional list of all possible themes (if None, uses union of both distributions)
    
    Returns:
        float: JSD value (range 0-1, where 0 = identical distributions, 1 = maximum divergence)
    """
    return calculate_jsd(predicted_themes, actual_themes, all_themes)

def calculate_review_metrics(prediction, actual, all_themes=None):
    """
    Calculate metrics for a single review.
    For logprobs mode: uses JSD for theme metrics.
    For confidence mode: uses rating, sentiment, and text delta only.
    """
    if not isinstance(prediction, dict) or not isinstance(actual, dict):
        return None

    # Initialize metrics dictionary
    metrics = {}

    # --- Rating and Sentiment Scores ---
    actual_rating = actual.get('rating', 3.0)
    predicted_rating = prediction.get('rating')
    rating_score = 0.0
    if predicted_rating is not None:
        try:
            rating_diff = abs(float(predicted_rating) - float(actual_rating))
            rating_score = max(0.0, 1.0 - (rating_diff / 4.0))
        except (ValueError, TypeError):
            rating_score = 0.0
    
    predicted_sentiment = str(prediction.get('sentiment', "")).strip().lower()
    actual_sentiment = str(actual.get('sentiment', "")).strip().lower()
    sentiment_score = 1.0 if predicted_sentiment and predicted_sentiment == actual_sentiment else 0.0

    metrics['rating_score'] = rating_score
    metrics['sentiment_score'] = sentiment_score

    # --- Theme JSD (for logprobs mode) ---
    predicted_themes = prediction.get('predicted_themes', {})
    actual_themes = actual.get('predicted_themes', [])
    
    # Calculate JSD for theme distributions
    theme_jsd = calculate_jsd(predicted_themes, actual_themes, all_themes)
    metrics['theme_jsd'] = theme_jsd
    
    # For backward compatibility, also include num_actual_themes
    if isinstance(actual_themes, list):
        metrics['num_actual_themes'] = len(actual_themes)
    elif isinstance(actual_themes, dict):
        metrics['num_actual_themes'] = len([t for t, p in actual_themes.items() if p > 0])
    else:
        metrics['num_actual_themes'] = 0

    # --- Overall Accuracy (using JSD for theme component) ---
    # Lower JSD is better, so convert to score: 1 - JSD (clamped to 0-1)
    theme_score = max(0.0, 1.0 - theme_jsd)
    WEIGHTS = {'rating': 0.4, 'sentiment': 0.3, 'theme': 0.3}
    overall_accuracy = (rating_score * WEIGHTS['rating']) + \
                       (sentiment_score * WEIGHTS['sentiment']) + \
                       (theme_score * WEIGHTS['theme'])

    metrics['overall_accuracy'] = overall_accuracy
    metrics['weights_used'] = WEIGHTS

    return metrics
