/**
 * PageHeader — shared in-page heading block (eyebrow, title, actions).
 *
 * Introduced in WP1 for the rebuilt Home; legacy pages migrate onto it
 * screen-by-screen in WP3.
 */
import React from "react";

export function PageHeader({
  eyebrow,
  title,
  actions,
}: {
  eyebrow?: string;
  title: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="shell-page-header">
      <div>
        {eyebrow && <p className="gd-eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
      </div>
      {actions && <div className="shell-page-actions">{actions}</div>}
    </div>
  );
}
