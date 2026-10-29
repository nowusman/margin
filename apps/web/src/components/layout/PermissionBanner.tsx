import React from "react";

type PermissionBannerProps = {
  limitedByPermissions: boolean;
};

export function PermissionBanner({
  limitedByPermissions,
}: PermissionBannerProps): React.JSX.Element | null {
  if (!limitedByPermissions) {
    return null;
  }

  return (
    <div className="permission-banner" role="status">
      Results are filtered by your access permissions. Some evidence may be hidden.
    </div>
  );
}
