import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ReservationTime from './ReservationTime';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-10-19T15:26:00+09:00'));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('reservation remaining time', () => {
  it.each(['2026-10-19 18:00:00', '2026-10-19T09:00:00Z'])(
    'shows a live countdown for the library timestamp %s', endTime => {
      render(<ReservationTime reservation={{ endTime }} fresh />);
      expect(screen.getByText('2시간 34분 남음')).toBeTruthy();
      expect(screen.getByText(/종료 10월 19일 18:00/)).toBeTruthy();
      act(() => vi.advanceTimersByTime(60000));
      expect(screen.getByText('2시간 33분 남음')).toBeTruthy();
    },
  );

  it('updates to the extended end time and marks stale information', () => {
    const { rerender } = render(<ReservationTime reservation={{ endTime: '2026-10-19 18:00:00' }} fresh />);
    rerender(<ReservationTime reservation={{ endTime: '2026-10-19 21:00:00' }} fresh={false} />);
    expect(screen.getByText('5시간 34분 남음')).toBeTruthy();
    expect(screen.getByText('마지막으로 확인한 종료 시각 기준')).toBeTruthy();
  });

  it('shows that the end time passed instead of a negative countdown', () => {
    render(<ReservationTime reservation={{ endTime: '2026-10-19 15:25:59' }} fresh />);
    expect(screen.getByText('종료 시각 지남')).toBeTruthy();
  });

  it('keeps a remaining-minutes-only response labelled as the last reported value', () => {
    render(<ReservationTime reservation={{ endTime: 'invalid', remainingTime: 154 }} fresh />);
    act(() => vi.advanceTimersByTime(60000));
    expect(screen.getByText('2시간 34분 남음')).toBeTruthy();
    expect(screen.getByText('마지막 조회 기준')).toBeTruthy();
  });

  it.each([null, '', true])('does not turn an unknown remaining time into zero or one minute (%s)', remainingTime => {
    render(<ReservationTime reservation={{ remainingTime }} fresh />);
    expect(screen.getByText('남은 시간 확인 필요')).toBeTruthy();
  });
});
