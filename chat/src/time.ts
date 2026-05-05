const LOCAL_DATE_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
});

const LOCAL_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  hour12: true,
  minute: "2-digit",
});

export function formatLocalTimestamp(value?: string | number | Date | null, fallback = "Not yet") {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return `${LOCAL_DATE_FORMATTER.format(date)} at ${LOCAL_TIME_FORMATTER.format(date)}`;
}
