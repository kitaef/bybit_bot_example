from sqlalchemy import Column, Integer, BigInteger, Float, String, Boolean, ForeignKey, \
    TIMESTAMP, create_engine, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import TypeDecorator
from config import DB_PASSWORD, DB_USER, DB_HOST, DB_NAME

sql_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
engine = create_engine(sql_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass

class Orders(Base):
    __tablename__ = 'orders'
    id = Column(Integer, primary_key=True)
    orderId = Column(String, name="order_id")
    symbol = Column(String)
    side = Column(String)
    orderType = Column(String, name="order_type")
    qty = Column(Float)
    price = Column(Float)
    stopLoss = Column(Float, name="stop_loss")
    create_time = Column(BigInteger)

class Positions(Base):
    __tablename__ = 'positions'
    id = Column(Integer, primary_key=True)
    symbol = Column(String)
    side = Column(String)
    avgPrice = Column(Float, name="average_price")
    markPrice = Column(Float, name='market_price')
    size = Column(Float)
    positionValue = Column(Float, name="position_value")
    unrealisedPnl = Column(Float, name="unrealised_pnl")
    createdTime = Column(BigInteger, name="created_time")
    updatedTime = Column(BigInteger, name="updated_time")
    __table_args__ = (UniqueConstraint(symbol),)


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
