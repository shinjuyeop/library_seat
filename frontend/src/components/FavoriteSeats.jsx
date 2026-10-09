import { favoriteSeats } from '../model';
import Icon from './Icon';

export default function FavoriteSeats({ data, busy, reachable, onRemove, onView }) {
  const seats = favoriteSeats(data);
  return <section className="card favorites-card" aria-labelledby="favorites-heading">
    <div className="section-heading"><h2 id="favorites-heading">선호좌석 <span className="muted">{seats.length}</span></h2>
      <button className="text-button" onClick={onView}>좌석 찾기에서 보기</button></div>
    {seats.length ? <ul className="favorite-list">{seats.map(seat => <li key={seat.key}>
      <span>{seat.roomName} · {seat.number}번</span>
      <button className="icon-button" aria-label={`${seat.roomName} ${seat.number}번 선호좌석 등록 취소`}
        disabled={busy || !reachable} onClick={() => onRemove(seat.key)}><Icon name="close" /></button>
    </li>)}</ul> : <p className="fine">좌석 찾기에서 좌석을 눌러 선호좌석을 등록하세요.</p>}
  </section>;
}
