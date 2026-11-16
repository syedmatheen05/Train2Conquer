"""Account-level operations shared by the user and admin areas."""
from models import FitnessProfile, OtpCode, Payment, User, WorkoutPlan, db


def delete_user_account(user_id):
    """Delete a user and their personal data. The caller commits.

    * profile, workout plans and pending verification codes are deleted;
    * payment rows are KEPT (financial audit trail) but detached from the user;
    * AI usage rows are kept for cost accounting (they hold no personal data).
    """
    user = db.session.get(User, user_id)
    if user is None:
        return False
    db.session.execute(db.delete(FitnessProfile).where(FitnessProfile.user_id == user_id))
    db.session.execute(db.delete(WorkoutPlan).where(WorkoutPlan.user_id == user_id))
    db.session.execute(db.update(Payment).where(Payment.user_id == user_id).values(user_id=None))
    db.session.execute(db.delete(OtpCode).where(OtpCode.email == user.email))
    db.session.execute(db.delete(User).where(User.id == user_id))
    return True
