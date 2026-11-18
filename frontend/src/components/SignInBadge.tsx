import { Link } from "react-router-dom";

export function SignInBadge() {
  return (
    <Link className="pill sign-in-flag" to="/organizer/sign-in">
      Sign-in check
    </Link>
  );
}
