import * as Menu from "@radix-ui/react-dropdown-menu";

export interface MenuChoice {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  title?: string;
}

/** An option that stays on or off (a chart's filing markers): ticked in the menu, which stays open. */
export interface MenuCheck {
  label: string;
  checked: boolean;
  onCheckedChange: (on: boolean) => void;
  disabled?: boolean;
  title?: string;
}

const isCheck = (it: MenuChoice | MenuCheck): it is MenuCheck => "checked" in it;

/** A button that opens a short menu: the secondary actions of a page's bar in one place, so the bar stays
 * on one row (Riley 101: Export CSV, Export SVG, Export PNG and two checkboxes side by side wrapped the
 * chart builder's toolbar onto three rows). A Radix DropdownMenu, never modal (a modal one locks
 * scrolling with an injected style the CSP refuses), styled like the company page's More. The button
 * says how many options are on, so a ticked one isn't hidden. */
export function MenuButton({ label, items, disabled = false, title, align = "end" }: {
  label: string;
  items: (MenuChoice | MenuCheck)[];
  disabled?: boolean;
  title?: string;
  align?: "start" | "end";
}) {
  const on = items.filter((it) => isCheck(it) && it.checked).length;
  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button className="btn more-btn" disabled={disabled} title={title}>
          {label}
          {on > 0 && <span className="menu-on" aria-label={`${on} on`}>{on}</span>}
          <span className="more-caret" aria-hidden="true" />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content className="menu" align={align} sideOffset={6} collisionPadding={12}>
          {items.map((it) => isCheck(it) ? (
            <Menu.CheckboxItem key={it.label} className="menu-item menu-check" checked={it.checked} disabled={it.disabled} title={it.title}
              onCheckedChange={(v) => it.onCheckedChange(v === true)} onSelect={(e) => e.preventDefault()}>
              <span className="menu-box" aria-hidden="true">
                <Menu.ItemIndicator className="menu-tick" />
              </span>
              {it.label}
            </Menu.CheckboxItem>
          ) : (
            <Menu.Item key={it.label} className="menu-item" disabled={it.disabled} title={it.title} onSelect={it.onSelect}>
              {it.label}
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
