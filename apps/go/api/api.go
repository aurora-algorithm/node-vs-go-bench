// Package api holds the types, the SQL (the same text as the TypeScript server), the pgx pool and the handlers.
// The server in cmd/server picks the JSON encoder.
package api

import (
	"context"
	"errors"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	orderSQL     = "SELECT id, customer_id, status, created_at FROM orders WHERE id = $1"
	itemsSQL     = "SELECT line, sku, qty, price_cents FROM order_items WHERE order_id = $1 ORDER BY line"
	pageSQL      = "SELECT id, customer_id, status, created_at FROM orders WHERE id > $1 ORDER BY id LIMIT $2"
	pageItemsSQL = "SELECT order_id, line, sku, qty, price_cents FROM order_items WHERE order_id = ANY($1) ORDER BY order_id, line"
	slowSQL      = "SELECT pg_sleep(0.005)"
)

type Item struct {
	Line       int32  `json:"line"`
	SKU        string `json:"sku"`
	Qty        int32  `json:"qty"`
	PriceCents int64  `json:"price_cents"`
}

type Order struct {
	ID         int64     `json:"id"`
	CustomerID int64     `json:"customer_id"`
	Status     string    `json:"status"`
	CreatedAt  time.Time `json:"created_at"`
	Items      []Item    `json:"items"`
	TotalCents int64     `json:"total_cents"`
}

type Page struct {
	Data  []Order `json:"data"`
	Count int     `json:"count"`
}

// Marshal is the JSON encoder a server plugs in (encoding/json or a faster drop-in).
type Marshal func(v any) ([]byte, error)

type Server struct {
	pool    *pgxpool.Pool
	marshal Marshal
}

func NewServer(ctx context.Context, marshal Marshal) (*Server, error) {
	cfg, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		return nil, err
	}
	size, err := strconv.Atoi(os.Getenv("POOL_SIZE"))
	if err != nil || size < 1 {
		return nil, errors.New("POOL_SIZE must be a positive integer")
	}
	cfg.MaxConns, cfg.MinConns = int32(size), int32(size)
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	return &Server{pool: pool, marshal: marshal}, nil
}

func (s *Server) Routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`"ok"`))
	})
	mux.HandleFunc("GET /orders/{id}", s.order(false))
	mux.HandleFunc("GET /slow/orders/{id}", s.order(true))
	mux.HandleFunc("GET /orders", s.page)
	return mux
}

func (s *Server) order(slow bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
		if err != nil {
			http.Error(w, "bad id", http.StatusUnprocessableEntity)
			return
		}
		ctx := r.Context()
		conn, err := s.pool.Acquire(ctx)
		if err != nil {
			http.Error(w, err.Error(), http.StatusServiceUnavailable)
			return
		}
		defer conn.Release()
		if slow {
			if _, err := conn.Exec(ctx, slowSQL); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
		}
		var o Order
		err = conn.QueryRow(ctx, orderSQL, id).
			Scan(&o.ID, &o.CustomerID, &o.Status, &o.CreatedAt)
		if errors.Is(err, pgx.ErrNoRows) {
			http.Error(w, "order not found", http.StatusNotFound)
			return
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		rows, err := conn.Query(ctx, itemsSQL, id)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		o.Items = make([]Item, 0, 10)
		for rows.Next() {
			var it Item
			if err := rows.Scan(&it.Line, &it.SKU, &it.Qty, &it.PriceCents); err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			o.Items = append(o.Items, it)
		}
		if err := rows.Err(); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		s.write(w, finish(o))
	}
}

func (s *Server) page(w http.ResponseWriter, r *http.Request) {
	after, limit, err := pageParams(r)
	if err != nil {
		http.Error(w, err.Error(), http.StatusUnprocessableEntity)
		return
	}
	ctx := r.Context()
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	defer conn.Release()
	rows, err := conn.Query(ctx, pageSQL, after, limit)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	orders := make([]Order, 0, limit)
	index := make(map[int64]int, limit)
	ids := make([]int64, 0, limit)
	for rows.Next() {
		var o Order
		if err := rows.Scan(&o.ID, &o.CustomerID, &o.Status, &o.CreatedAt); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		o.Items = make([]Item, 0, 10)
		index[o.ID] = len(orders)
		orders = append(orders, o)
		ids = append(ids, o.ID)
	}
	if err := rows.Err(); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	rows, err = conn.Query(ctx, pageItemsSQL, ids)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	for rows.Next() {
		var orderID int64
		var it Item
		if err := rows.Scan(&orderID, &it.Line, &it.SKU, &it.Qty, &it.PriceCents); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		i := index[orderID]
		orders[i].Items = append(orders[i].Items, it)
	}
	if err := rows.Err(); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	for i := range orders {
		orders[i] = finish(orders[i])
	}
	s.write(w, Page{Data: orders, Count: len(orders)})
}

func pageParams(r *http.Request) (int64, int, error) {
	after, limit := int64(0), 50
	q := r.URL.Query()
	if v := q.Get("after"); v != "" {
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil {
			return 0, 0, errors.New("bad after")
		}
		after = n
	}
	if v := q.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 500 {
			return 0, 0, errors.New("bad limit")
		}
		limit = n
	}
	return after, limit, nil
}

func finish(o Order) Order {
	o.CreatedAt = o.CreatedAt.UTC()
	o.TotalCents = 0
	for _, it := range o.Items {
		o.TotalCents += int64(it.Qty) * it.PriceCents
	}
	return o
}

func (s *Server) write(w http.ResponseWriter, v any) {
	body, err := s.marshal(v)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(body)
}
